"""Transparent path selection for patient-specific clarification burden."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from typing import Any

from ..contracts import ConfirmationStatus, NextAction
from .burden_contracts import (
    AcquisitionDecision,
    AcquisitionMode,
    AcquisitionOption,
    AcquisitionPolicyId,
    ActionStatus,
    AvailabilityStructure,
    DecisionTrace,
    DirectCostBand,
    PatientBurdenInput,
    PatientBurdenProfile,
    PatientInputStatus,
    PreferenceMode,
    RemovedOption,
)
from .contracts import InteractivePolicyView, InteractiveSnapshot
from .coverage_policy import choose_exact_coverage_fact


_PROFILE_VALUE_FIELDS = (
    "time_urgency_0_to_3",
    "fatigue_or_mobility_limit_0_to_3",
    "travel_constraint_0_to_3",
    "cost_sensitivity_0_to_3",
    "procedure_aversion_0_to_3",
    "treatment_change_aversion_0_to_3",
)
_INPUT_FIELDS = (*_PROFILE_VALUE_FIELDS, "preference_mode", "stated_limits")
_COST_RANK = {
    DirectCostBand.NONE: 0,
    DirectCostBand.LOW: 1,
    DirectCostBand.MEDIUM: 2,
    DirectCostBand.HIGH: 3,
    DirectCostBand.UNKNOWN: None,
}
_LEGACY_ROUTE_COST = {
    NextAction.ASK_PATIENT: 1,
    NextAction.LOOKUP_RECORD: 2,
    NextAction.REQUEST_VERIFICATION: 3,
}
_EXISTING_MODES = {
    AcquisitionMode.INTERNAL_RECORD,
    AcquisitionMode.OUTSIDE_RECORD,
    AcquisitionMode.EXISTING_OFFICIAL_RESULT,
}
_NEW_MODES = {
    AcquisitionMode.NEW_NONINVASIVE_TEST,
    AcquisitionMode.NEW_INVASIVE_OR_TREATMENT_CHANGE,
}
_FORMAL_ASSESSMENT_FACTS = {
    "er_positive",
    "her2_positive",
    "madrs",
    "active_suicide_risk",
    "active_substance_disorder",
}


def build_patient_burden_profile(
    profile_id: str,
    patient_input: PatientBurdenInput | Mapping[str, Any] | None = None,
) -> PatientBurdenProfile:
    """Apply safe defaults without turning unanswered burden into zero."""

    if patient_input is None:
        parsed = PatientBurdenInput()
        supplied: set[str] = set()
    elif isinstance(patient_input, PatientBurdenInput):
        parsed = patient_input
        supplied = set(parsed.model_fields_set)
    else:
        supplied = set(patient_input)
        parsed = PatientBurdenInput.model_validate(patient_input)
    meaningful = {
        name for name in supplied if getattr(parsed, name, None) is not None
    }
    if not meaningful:
        status = PatientInputStatus.ABSENT
    elif all(getattr(parsed, name) is not None for name in _PROFILE_VALUE_FIELDS) and (
        parsed.preference_mode is not None
    ):
        status = PatientInputStatus.COMPLETE
    else:
        status = PatientInputStatus.PARTIAL
    defaulted = [name for name in _INPUT_FIELDS if name not in meaningful]
    return PatientBurdenProfile(
        profile_id=profile_id,
        input_status=status,
        **{name: getattr(parsed, name) for name in _PROFILE_VALUE_FIELDS},
        preference_mode=parsed.preference_mode or PreferenceMode.BALANCED,
        stated_limits=parsed.stated_limits,
        defaulted_fields=defaulted,
    )


def benchmark_patient_profiles() -> tuple[PatientBurdenProfile, ...]:
    """Three declared synthetic situations; these are not patient estimates."""

    return (
        build_patient_burden_profile(
            "low_extra_burden",
            {
                "time_urgency_0_to_3": 1,
                "fatigue_or_mobility_limit_0_to_3": 0,
                "travel_constraint_0_to_3": 0,
                "cost_sensitivity_0_to_3": 0,
                "procedure_aversion_0_to_3": 0,
                "treatment_change_aversion_0_to_3": 1,
                "preference_mode": "balanced",
                "stated_limits": {"explicitly_no_limits": True},
            },
        ),
        build_patient_burden_profile(
            "mobility_cost_constrained",
            {
                "time_urgency_0_to_3": 1,
                "fatigue_or_mobility_limit_0_to_3": 3,
                "travel_constraint_0_to_3": 3,
                "cost_sensitivity_0_to_3": 3,
                "procedure_aversion_0_to_3": 2,
                "treatment_change_aversion_0_to_3": 2,
                "preference_mode": "least_extra_burden",
                "stated_limits": {
                    "max_additional_visits": 0,
                    "max_direct_cost_band": "low",
                    "max_physical_burden": 1,
                    "max_medical_risk": 1,
                    "allow_new_tests": False,
                    "allow_treatment_change": False,
                },
            },
        ),
        build_patient_burden_profile(
            "time_urgent",
            {
                "time_urgency_0_to_3": 3,
                "fatigue_or_mobility_limit_0_to_3": 1,
                "travel_constraint_0_to_3": 1,
                "cost_sensitivity_0_to_3": 1,
                "procedure_aversion_0_to_3": 1,
                "treatment_change_aversion_0_to_3": 2,
                "preference_mode": "fastest",
                "stated_limits": {
                    "max_medical_risk": 1,
                    "allow_new_tests": True,
                    "allow_treatment_change": False,
                },
            },
        ),
    )


def _option(
    fact_id: str,
    mode: AcquisitionMode,
    action: NextAction,
    *,
    available: bool,
    delay: float | None,
    visit: bool | None,
    cost: DirectCostBand,
    physical: int | None,
    emotional: int | None,
    risk: int | None,
    treatment: int | None,
    patient_choice: bool = False,
    clinician_authorization: bool = False,
    source_note: str,
) -> AcquisitionOption:
    return AcquisitionOption(
        option_id=f"{fact_id}:{mode.value}",
        fact_id=fact_id,
        action=action,
        acquisition_mode=mode,
        available_now=available,
        expected_delay_hours=delay,
        visit_required=visit,
        direct_cost_band=cost,
        physical_burden_0_to_3=physical,
        emotional_burden_0_to_3=emotional,
        medical_risk_0_to_3=risk,
        treatment_disruption_0_to_3=treatment,
        new_test_required=mode in _NEW_MODES,
        requires_patient_choice=patient_choice,
        requires_clinician_authorization=clinician_authorization,
        source_note=source_note,
    )


def _new_confirmation_option(fact_id: str, fact_code: str) -> AcquisitionOption:
    if fact_code in _FORMAL_ASSESSMENT_FACTS:
        return _option(
            fact_id,
            AcquisitionMode.CLINICIAN_JUDGMENT,
            NextAction.REQUEST_VERIFICATION,
            available=True,
            delay=24,
            visit=True,
            cost=DirectCostBand.UNKNOWN,
            physical=0,
            emotional=1,
            risk=0,
            treatment=0,
            patient_choice=True,
            clinician_authorization=True,
            source_note="합성 평가에서 별도 공식 평가가 필요한 경로",
        )
    return _option(
        fact_id,
        AcquisitionMode.NEW_NONINVASIVE_TEST,
        NextAction.REQUEST_VERIFICATION,
        available=True,
        delay=48,
        visit=True,
        cost=DirectCostBand.MEDIUM,
        physical=1,
        emotional=1,
        risk=1,
        treatment=0,
        patient_choice=True,
        clinician_authorization=True,
        source_note="합성 평가에서 새 비침습 확인이 필요한 경로",
    )


def build_acquisition_catalog(
    view: InteractivePolicyView,
    availability: AvailabilityStructure,
) -> dict[str, tuple[AcquisitionOption, ...]]:
    """Expand each public missing fact into inspectable acquisition paths."""

    catalog: dict[str, tuple[AcquisitionOption, ...]] = {}
    for fact in view.available_information:
        original_action = fact.available_actions[0]
        fact_code = fact.fact_id.rsplit("-", 1)[-1]
        options: list[AcquisitionOption] = []
        if original_action is NextAction.ASK_PATIENT:
            options.append(
                _option(
                    fact.fact_id,
                    AcquisitionMode.PATIENT_REPORT,
                    NextAction.ASK_PATIENT,
                    available=True,
                    delay=0.25,
                    visit=False,
                    cost=DirectCostBand.NONE,
                    physical=0,
                    emotional=1,
                    risk=0,
                    treatment=0,
                    source_note="환자가 답할 수 있는 합성 확인 항목",
                )
            )
        elif original_action is NextAction.LOOKUP_RECORD:
            options.extend(
                (
                    _option(
                        fact.fact_id,
                        AcquisitionMode.INTERNAL_RECORD,
                        NextAction.LOOKUP_RECORD,
                        available=(
                            availability
                            is AvailabilityStructure.EXISTING_DATA_CENTERED
                        ),
                        delay=2,
                        visit=False,
                        cost=DirectCostBand.NONE,
                        physical=0,
                        emotional=0,
                        risk=0,
                        treatment=0,
                        source_note="합성 병원 내부 기록 조회 경로",
                    ),
                    _option(
                        fact.fact_id,
                        AcquisitionMode.OUTSIDE_RECORD,
                        NextAction.LOOKUP_RECORD,
                        available=True,
                        delay=(48 if availability is AvailabilityStructure.EXISTING_DATA_CENTERED else 72),
                        visit=False,
                        cost=DirectCostBand.LOW,
                        physical=0,
                        emotional=1,
                        risk=0,
                        treatment=0,
                        source_note="합성 외부 기록 요청 경로",
                    ),
                )
            )
        elif original_action is NextAction.REQUEST_VERIFICATION:
            options.append(
                _option(
                    fact.fact_id,
                    AcquisitionMode.EXISTING_OFFICIAL_RESULT,
                    NextAction.REQUEST_VERIFICATION,
                    available=(
                        availability is AvailabilityStructure.EXISTING_DATA_CENTERED
                    ),
                    delay=8,
                    visit=False,
                    cost=DirectCostBand.NONE,
                    physical=0,
                    emotional=0,
                    risk=0,
                    treatment=0,
                    source_note="합성 기존 공식 결과 확인 경로",
                )
            )
            options.append(_new_confirmation_option(fact.fact_id, fact_code))
        else:
            raise ValueError(f"unsupported public fact route: {original_action}")
        catalog[fact.fact_id] = tuple(options)
    return catalog


def current_fact_impacts(
    view: InteractivePolicyView,
    snapshot: InteractiveSnapshot,
    revealed_fact_ids: frozenset[str],
) -> dict[str, tuple[int, int, tuple[str, ...], tuple[str, ...]]]:
    """Count currently unresolved trials and criteria for each public fact."""

    decision_by_trial = {item.trial_id: item for item in snapshot.decisions}
    criterion_to_trial = {
        criterion.criterion_id: trial.trial_id
        for trial in view.trials
        for criterion in trial.criteria
    }
    result = {}
    for fact in view.available_information:
        if fact.fact_id in revealed_fact_ids:
            continue
        trial_ids: set[str] = set()
        criterion_ids: list[str] = []
        for criterion_id in fact.related_criterion_ids:
            trial_id = criterion_to_trial[criterion_id]
            decision = decision_by_trial[trial_id]
            if decision.confirmation_status in {
                ConfirmationStatus.CONFIRMED,
                ConfirmationStatus.INELIGIBLE,
            }:
                continue
            trial_ids.add(trial_id)
            criterion_ids.append(criterion_id)
        result[fact.fact_id] = (
            len(trial_ids),
            len(criterion_ids),
            tuple(sorted(trial_ids)),
            tuple(sorted(criterion_ids)),
        )
    return result


def _unknown_limit_fields(
    option: AcquisitionOption, profile: PatientBurdenProfile
) -> list[str]:
    limits = profile.stated_limits
    if limits is None or limits.explicitly_no_limits:
        return []
    unknown = []
    if limits.max_additional_visits is not None and option.visit_required is None:
        unknown.append("visit_required")
    if (
        limits.max_direct_cost_band is not None
        and option.direct_cost_band is DirectCostBand.UNKNOWN
    ):
        unknown.append("direct_cost_band")
    if limits.max_physical_burden is not None and option.physical_burden_0_to_3 is None:
        unknown.append("physical_burden_0_to_3")
    if limits.max_medical_risk is not None and option.medical_risk_0_to_3 is None:
        unknown.append("medical_risk_0_to_3")
    return unknown


def explicit_limit_violations(
    option: AcquisitionOption,
    profile: PatientBurdenProfile,
    selected_options: Sequence[AcquisitionOption] = (),
) -> list[str]:
    limits = profile.stated_limits
    if limits is None or limits.explicitly_no_limits:
        return []
    violations = []
    if (
        limits.max_additional_visits is not None
        and option.visit_required is True
        and sum(item.visit_required is True for item in selected_options) + 1
        > limits.max_additional_visits
    ):
        violations.append("추가 방문 한도 초과")
    option_cost = _COST_RANK[option.direct_cost_band]
    limit_cost = (
        _COST_RANK[limits.max_direct_cost_band]
        if limits.max_direct_cost_band is not None
        else None
    )
    if option_cost is not None and limit_cost is not None and option_cost > limit_cost:
        violations.append("직접 비용 한도 초과")
    if (
        limits.max_physical_burden is not None
        and option.physical_burden_0_to_3 is not None
        and option.physical_burden_0_to_3 > limits.max_physical_burden
    ):
        violations.append("신체 부담 한도 초과")
    if (
        limits.max_medical_risk is not None
        and option.medical_risk_0_to_3 is not None
        and option.medical_risk_0_to_3 > limits.max_medical_risk
    ):
        violations.append("의료 위험 한도 초과")
    if limits.allow_new_tests is False and option.new_test_required:
        violations.append("새 검사 거부")
    if (
        limits.allow_treatment_change is False
        and option.acquisition_mode
        is AcquisitionMode.NEW_INVASIVE_OR_TREATMENT_CHANGE
    ):
        violations.append("치료 변경 거부")
    return violations


def _burden_vector(option: AcquisitionOption) -> tuple[int | float | None, ...]:
    return (
        int(option.new_test_required),
        option.medical_risk_0_to_3,
        option.treatment_disruption_0_to_3,
        int(option.visit_required) if option.visit_required is not None else None,
        _COST_RANK[option.direct_cost_band],
        option.physical_burden_0_to_3,
        option.emotional_burden_0_to_3,
        option.expected_delay_hours,
    )


def _dominates(left: AcquisitionOption, right: AcquisitionOption) -> bool:
    left_values = _burden_vector(left)
    right_values = _burden_vector(right)
    if any(value is None for value in (*left_values, *right_values)):
        return False
    return all(a <= b for a, b in zip(left_values, right_values, strict=True)) and any(
        a < b for a, b in zip(left_values, right_values, strict=True)
    )


def _dimension_value(
    option: AcquisitionOption,
    impact: tuple[int, int, tuple[str, ...], tuple[str, ...]],
    name: str,
    preferred_fact_id: str | None = None,
) -> int | float | None:
    values: dict[str, int | float | None] = {
        "exact_coverage_choice": int(option.fact_id == preferred_fact_id),
        "affected_trials": impact[0],
        "affected_criteria": impact[1],
        "legacy_route_cost": _LEGACY_ROUTE_COST[option.action],
        "new_test": int(option.new_test_required),
        "medical_risk": option.medical_risk_0_to_3,
        "treatment_disruption": option.treatment_disruption_0_to_3,
        "visit": int(option.visit_required) if option.visit_required is not None else None,
        "cost": _COST_RANK[option.direct_cost_band],
        "physical_burden": option.physical_burden_0_to_3,
        "emotional_burden": option.emotional_burden_0_to_3,
        "delay": option.expected_delay_hours,
    }
    return values[name]


def _ordering(
    policy_id: AcquisitionPolicyId, profile: PatientBurdenProfile
) -> list[tuple[str, str]]:
    impact = [
        ("exact_coverage_choice", "max"),
        ("affected_trials", "max"),
        ("affected_criteria", "max"),
    ]
    least = [
        ("new_test", "min"),
        ("medical_risk", "min"),
        ("treatment_disruption", "min"),
        ("visit", "min"),
        ("cost", "min"),
        ("physical_burden", "min"),
        ("emotional_burden", "min"),
        ("delay", "min"),
    ]
    if policy_id is AcquisitionPolicyId.IMPACT_ONLY:
        return impact
    if policy_id is AcquisitionPolicyId.FIXED_ROUTE_COST:
        return [*impact, ("legacy_route_cost", "min")]
    if policy_id is AcquisitionPolicyId.LEAST_EXTRA_BURDEN:
        return [*least, *impact]
    if policy_id is AcquisitionPolicyId.ALL_INFORMATION:
        return impact
    if profile.preference_mode is PreferenceMode.FASTEST or (
        profile.time_urgency_0_to_3 is not None
        and profile.time_urgency_0_to_3 >= 2
    ):
        return [*impact, ("delay", "min"), *least[:-1]]
    if profile.preference_mode is PreferenceMode.LEAST_EXTRA_BURDEN:
        priority_scores = {
            "visit": max(
                profile.fatigue_or_mobility_limit_0_to_3 or 0,
                profile.travel_constraint_0_to_3 or 0,
            ),
            "cost": profile.cost_sensitivity_0_to_3 or 0,
            "physical_burden": profile.procedure_aversion_0_to_3 or 0,
            "treatment_disruption": profile.treatment_change_aversion_0_to_3 or 0,
        }
        prioritized = sorted(
            priority_scores,
            key=lambda name: (-priority_scores[name], name),
        )
        names = ["new_test", "medical_risk", *prioritized]
        names.extend(name for name, _ in least if name not in names)
        return [(name, "min") for name in names] + impact
    return [*impact, *least]


def select_acquisition_option(
    *,
    view: InteractivePolicyView,
    snapshot: InteractiveSnapshot,
    revealed_fact_ids: frozenset[str],
    unavailable_fact_ids: frozenset[str] = frozenset(),
    catalog: Mapping[str, Sequence[AcquisitionOption]],
    profile: PatientBurdenProfile,
    policy_id: AcquisitionPolicyId,
    selected_options: Sequence[AcquisitionOption] = (),
) -> AcquisitionDecision:
    """Select one path with a replayable, field-by-field decision trace."""

    impacts = current_fact_impacts(view, snapshot, revealed_fact_ids)
    considered = [
        option
        for fact_id in sorted(catalog)
        if fact_id not in revealed_fact_ids
        for option in catalog[fact_id]
    ]
    removed: list[RemovedOption] = []
    active: list[AcquisitionOption] = []
    for option in considered:
        impact = impacts.get(option.fact_id)
        if option.fact_id in unavailable_fact_ids:
            removed.append(
                RemovedOption(
                    option_id=option.option_id,
                    reason="이번 실행에서 확인했지만 정보를 얻지 못한 항목",
                )
            )
        elif impact is None or impact[0] == 0:
            removed.append(RemovedOption(option_id=option.option_id, reason="현재 미해결 후보에 영향 없음"))
        elif not option.available_now:
            removed.append(RemovedOption(option_id=option.option_id, reason="현재 사용할 수 없는 경로"))
        elif (
            policy_id is AcquisitionPolicyId.PATIENT_ADAPTIVE
            and explicit_limit_violations(option, profile, selected_options)
        ):
            removed.append(
                RemovedOption(
                    option_id=option.option_id,
                    reason="; ".join(
                        explicit_limit_violations(
                            option, profile, selected_options
                        )
                    ),
                )
            )
        else:
            active.append(option)

    existing_by_fact: dict[str, bool] = defaultdict(bool)
    for option in active:
        existing_by_fact[option.fact_id] |= option.acquisition_mode in _EXISTING_MODES
    kept = []
    for option in active:
        if (
            existing_by_fact[option.fact_id]
            and option.acquisition_mode not in _EXISTING_MODES
        ):
            removed.append(
                RemovedOption(
                    option_id=option.option_id,
                    reason="같은 사실의 기존 자료 경로 우선",
                )
            )
        else:
            kept.append(option)
    active = kept

    dominated_ids: set[str] = set()
    for left in active:
        for right in active:
            if left.fact_id == right.fact_id and left != right and _dominates(left, right):
                dominated_ids.add(right.option_id)
    if dominated_ids:
        active = [item for item in active if item.option_id not in dominated_ids]
        removed.extend(
            RemovedOption(option_id=item, reason="같은 사실의 다른 경로보다 모든 부담이 같거나 큼")
            for item in sorted(dominated_ids)
        )

    preferred_fact_id = None
    if policy_id is AcquisitionPolicyId.PATIENT_ADAPTIVE and active:
        preferred_fact_id = choose_exact_coverage_fact(
            view=view,
            snapshot=snapshot,
            revealed_fact_ids=revealed_fact_ids,
            # Failed attempts still consume an external-action slot.  Counting
            # only revealed facts would let the planner look farther ahead
            # than the workflow can actually execute after an unavailable
            # record or unanswered question.
            remaining_budget=max(0, view.action_budget - len(selected_options)),
            allowed_fact_ids={item.fact_id for item in active},
        )

    ordering = _ordering(policy_id, profile)
    trace_base = {
        "considered_option_ids": [item.option_id for item in considered],
        "removed_options": removed,
        "applied_ordering_rule": [f"{name}:{direction}" for name, direction in ordering],
    }
    if not active:
        return AcquisitionDecision(
            policy_id=policy_id,
            action_status=ActionStatus.DEFERRED,
            selection_reason="현재 사용할 수 있고 환자가 밝힌 한도 안에 있는 확인 경로가 없다.",
            decision_trace=DecisionTrace(**trace_base),
        )

    remaining = list(active)
    first_difference = None
    unresolved_unknowns: list[str] = []
    for name, direction in ordering:
        values = [
            _dimension_value(
                item,
                impacts[item.fact_id],
                name,
                preferred_fact_id,
            )
            for item in remaining
        ]
        known = [value for value in values if value is not None]
        if not known:
            continue
        if len(known) != len(values):
            unresolved_unknowns.append(name)
            continue
        best = max(known) if direction == "max" else min(known)
        narrowed = [
            item
            for item, value in zip(remaining, values, strict=True)
            if value == best
        ]
        if len(narrowed) < len(remaining) and first_difference is None:
            first_difference = f"{name}:{direction}={best}"
        remaining = narrowed
        if len(remaining) == 1:
            break

    if len(remaining) > 1 and unresolved_unknowns:
        return AcquisitionDecision(
            policy_id=policy_id,
            alternative_options=sorted(remaining, key=lambda item: item.option_id),
            action_status=ActionStatus.DEFERRED,
            selection_reason="부담값이 확인되지 않아 경로를 하나로 정하지 않았다.",
            decision_trace=DecisionTrace(
                **trace_base,
                first_decisive_difference=first_difference,
                unresolved_unknown_fields=sorted(set(unresolved_unknowns)),
            ),
        )

    selected = min(remaining, key=lambda item: item.option_id)
    if first_difference is None and len(active) > 1:
        first_difference = "모든 공개 비교값이 같아 option_id 순서로 고정"
    selected_unknown_limits = _unknown_limit_fields(selected, profile)
    if selected.requires_clinician_authorization:
        status = ActionStatus.AWAITING_CLINICIAN_AUTHORIZATION
    elif selected.requires_patient_choice or selected_unknown_limits:
        status = ActionStatus.AWAITING_PATIENT_CHOICE
    else:
        status = ActionStatus.RECOMMENDED
    alternatives = sorted(
        (item for item in active if item.option_id != selected.option_id),
        key=lambda item: item.option_id,
    )
    return AcquisitionDecision(
        policy_id=policy_id,
        selected_option=selected,
        alternative_options=alternatives,
        action_status=status,
        selection_reason=(
            "공개된 영향과 부담 항목을 정해진 순서대로 비교해 이 경로를 골랐다."
        ),
        decision_trace=DecisionTrace(
            **trace_base,
            first_decisive_difference=first_difference,
            unresolved_unknown_fields=sorted(set(selected_unknown_limits)),
        ),
    )


def option_cost_rank(option: AcquisitionOption) -> int:
    """Return a reportable ordinal; unknown cost contributes no invented value."""

    value = _COST_RANK[option.direct_cost_band]
    return value if value is not None else 0


def option_is_dominated(
    option: AcquisitionOption, catalog: Mapping[str, Sequence[AcquisitionOption]]
) -> bool:
    return any(
        other.available_now
        and other.option_id != option.option_id
        and _dominates(other, option)
        for other in catalog.get(option.fact_id, ())
    )
