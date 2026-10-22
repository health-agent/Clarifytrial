"""Opt-in decision-aware planning with patient-feasible acquisition paths.

This research candidate shares the existing eligibility engine and scenario
planner.  It changes information selection, not evidence or trial decisions.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from ..contracts import PatientState
from .burden_contracts import (
    AcquisitionDecision, AcquisitionOption, AcquisitionPolicyId, ActionStatus,
    DecisionTrace, PatientBurdenProfile,
)
from .burden_policy import (
    _unknown_limit_fields, current_fact_impacts, explicit_limit_violations,
    select_acquisition_option,
)
from .contracts import (
    ExactPolicyChoice, ExactPolicyObjective, InteractivePolicyView,
    InteractiveSnapshot, ScenarioDistribution,
)
from .exact_policy import ConstrainedQuestionPlanner


@dataclass(frozen=True, slots=True)
class PlannedAcquisition:
    policy_id: str
    decision: AcquisitionDecision
    planning: ExactPolicyChoice | None


def feasible_options(
    catalog: Mapping[str, Sequence[AcquisitionOption]],
    profile: PatientBurdenProfile,
    selected_options: Sequence[AcquisitionOption] = (),
) -> dict[str, list[AcquisitionOption]]:
    """Return known-feasible paths; unknown limit fields are not assumed free."""

    return {
        fact_id: [
            option for option in options
            if option.available_now
            and not explicit_limit_violations(option, profile, selected_options)
            and not _unknown_limit_fields(option, profile)
        ]
        for fact_id, options in catalog.items()
    }


def remaining_visits(
    profile: PatientBurdenProfile, selected: Sequence[AcquisitionOption] = (),
) -> int | None:
    limits = profile.stated_limits
    if limits is None or limits.max_additional_visits is None:
        return None
    return max(0, limits.max_additional_visits - sum(
        option.visit_required is True for option in selected
    ))


def plan_acquisition(
    *,
    view: InteractivePolicyView,
    initial_state: PatientState,
    snapshot: InteractiveSnapshot,
    distribution: ScenarioDistribution,
    catalog: Mapping[str, Sequence[AcquisitionOption]],
    profile: PatientBurdenProfile,
    revealed_fact_ids: frozenset[str],
    selected_options: Sequence[AcquisitionOption] = (),
    unavailable_fact_ids: frozenset[str] = frozenset(),
    objective: ExactPolicyObjective = ExactPolicyObjective.EXPECTED,
    planning_horizon: int | None = None,
    planner: ConstrainedQuestionPlanner | None = None,
) -> PlannedAcquisition:
    """Plan both eligibility directions under action and total visit limits.

    The answer distribution must be supplied independently of evaluation answers.
    Inaccessible facts remain in every possible world but cannot be selected.
    Each visit is charged per acquisition, matching the existing burden model;
    combining multiple tests in one hospital visit is not modeled here.
    """

    if planning_horizon is not None and planning_horizon <= 0:
        raise ValueError("planning_horizon must be positive")
    horizon_label = "" if planning_horizon is None else f"_horizon_{planning_horizon}"
    policy_id = f"decision_aware_acquisition_{objective.value}{horizon_label}_v1"
    budget = max(0, view.action_budget - len(selected_options))
    if planning_horizon is not None:
        budget = min(budget, planning_horizon)
    impacts = current_fact_impacts(view, snapshot, revealed_fact_ids)
    feasible = feasible_options(catalog, profile, selected_options)
    active = {
        fact_id: options for fact_id, options in feasible.items()
        if options and fact_id not in unavailable_fact_ids
    }
    if not any(
        fact_id not in revealed_fact_ids and impacts.get(fact_id, (0,))[0] > 0
        for fact_id in active
    ) or budget == 0:
        return PlannedAcquisition(
            policy_id,
            AcquisitionDecision(
                policy_id=AcquisitionPolicyId.PATIENT_ADAPTIVE,
                action_status=ActionStatus.DEFERRED,
                selection_reason="확인 한도가 끝났거나 현재 허용된 확인 경로가 없다.",
                decision_trace=DecisionTrace(
                    considered_option_ids=[option.option_id for options in catalog.values() for option in options],
                    removed_options=[], applied_ordering_rule=["action_budget", "known_feasible_paths"],
                ),
            ),
            None,
        )
    units = {
        fact_id: min(int(option.visit_required is True) for option in options)
        for fact_id, options in active.items()
    }
    if planner is None:
        planner = ConstrainedQuestionPlanner(view, initial_state, distribution)
    elif planner.case_id != view.case_id or planner.initial_state != initial_state:
        raise ValueError("cached planner must belong to the same case and initial state")
    choice = planner.choose(
        snapshot=snapshot, revealed_fact_ids=revealed_fact_ids,
        remaining_budget=budget, allowed_fact_ids=set(active),
        acquisition_units_by_fact=units,
        remaining_acquisition_units=remaining_visits(profile, selected_options),
        objective=objective,
    )
    fact_id = choice.target_fact_id
    if fact_id is None:
        return PlannedAcquisition(
            policy_id,
            AcquisitionDecision(
                policy_id=AcquisitionPolicyId.PATIENT_ADAPTIVE,
                action_status=ActionStatus.DEFERRED,
                selection_reason="허용된 확인과 남은 횟수에서 추가 상태 개선을 찾지 못했다.",
                decision_trace=DecisionTrace(
                    considered_option_ids=[option.option_id for options in active.values() for option in options],
                    removed_options=[], applied_ordering_rule=[f"decision_tree:{objective.value}"],
                ),
            ),
            choice,
        )
    # A plan charged for a no-visit path must execute a path with that charge.
    # Other burden dimensions retain the existing patient-specific ordering.
    fact_options = active[fact_id]
    if remaining_visits(profile, selected_options) is not None:
        fact_options = [
            option for option in fact_options
            if int(option.visit_required is True) == units[fact_id]
        ]
    decision = select_acquisition_option(
        view=view, snapshot=snapshot, revealed_fact_ids=revealed_fact_ids,
        catalog={fact_id: fact_options}, profile=profile,
        policy_id=AcquisitionPolicyId.PATIENT_ADAPTIVE,
        selected_options=selected_options,
    )
    trace = decision.decision_trace.model_copy(update={
        "applied_ordering_rule": [
            f"decision_tree:{objective.value}", "cumulative_visit_limit",
            *decision.decision_trace.applied_ordering_rule,
        ],
    })
    decision = decision.model_copy(update={
        "selection_reason": (
            "공개 조건과 개발군의 답 분포에서 적격·부적격으로 끝나는 답변 가지를 "
            "함께 계산하고, 남은 확인 횟수와 총 방문 한도 안에서 다음 정보를 골랐다."
        ),
        "decision_trace": trace,
    })
    return PlannedAcquisition(policy_id, decision, choice)


__all__ = ["PlannedAcquisition", "feasible_options", "plan_acquisition", "remaining_visits"]
