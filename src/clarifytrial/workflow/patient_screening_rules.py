"""Deterministic state, selection, and reporting rules for patient screening."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence

from ..agents import CoordinatorRoute, ReviewDecision
from ..contracts import (
    AgentAction,
    CandidateStatus,
    ConfirmationStatus,
    CriterionAssessment,
    NextEvidenceRequest,
    NextAction,
    PatientState,
    TrialDecision,
)
from ..decision_rules import aggregate_trial_decision
from ..interactive.burden_benchmark import build_guidance_output
from ..interactive.burden_contracts import (
    AcquisitionDecision,
    AcquisitionOption,
    AcquisitionPolicyId,
    ActionStatus,
    DecisionTrace,
    PatientBurdenProfile,
)
from ..interactive.burden_policy import select_acquisition_option
from ..interactive.contracts import (
    InteractivePolicyView,
    InteractivePublicFact,
    InteractiveSnapshot,
    InteractiveTrial,
)
from ..reporting import (
    build_ineligible_boundary_differences,
    build_trial_reconsideration_summaries,
)
from ..settings import EpisodeSettings
from .patient_screening_contracts import (
    PatientScreeningActionRecord,
    PatientScreeningCase,
    PatientScreeningResult,
    PatientScreeningSnapshot,
    PatientScreeningStopReason,
    ScreeningTrial,
)


def group_acquisition_options(
    options: Sequence[AcquisitionOption],
) -> dict[str, tuple[AcquisitionOption, ...]]:
    """Group acquisition paths by the missing fact they can obtain."""

    grouped: dict[str, list[AcquisitionOption]] = defaultdict(list)
    for option in options:
        grouped[option.fact_id].append(option)
    return {
        fact_id: tuple(sorted(items, key=lambda item: item.option_id))
        for fact_id, items in grouped.items()
    }


def build_policy_view(
    case: PatientScreeningCase, action_budget: int = 0
) -> InteractivePolicyView:
    """Adapt visible workflow input to the tested multi-trial policy contract."""

    return InteractivePolicyView(
        case_id=case.case_id,
        disease_group=case.disease_group,
        trials=[
            InteractiveTrial(
                trial_id=item.trial_id,
                criteria=item.criteria,
                eligibility_logic=item.eligibility_logic,
            )
            for item in case.trials
        ],
        available_information=[
            InteractivePublicFact(
                fact_id=item.fact_id,
                description=item.description,
                available_actions=item.acceptable_actions,
                related_criterion_ids=item.related_criterion_ids,
            )
            for item in case.evidence_requests
        ],
        action_budget=action_budget,
    )


def interactive_snapshot(
    patient_state: PatientState,
    decisions: Mapping[str, TrialDecision],
) -> InteractiveSnapshot:
    return InteractiveSnapshot(
        patient_state=patient_state,
        decisions=sorted(decisions.values(), key=lambda item: item.trial_id),
    )


def history_snapshot(
    cycle: int,
    reason: str,
    decisions: Mapping[str, TrialDecision],
) -> PatientScreeningSnapshot:
    return PatientScreeningSnapshot(
        cycle=cycle,
        reason=reason,
        decisions=sorted(decisions.values(), key=lambda item: item.trial_id),
    )


def pending_requests(
    decisions: Mapping[str, TrialDecision],
    request_by_id: Mapping[str, NextEvidenceRequest],
    review_requested_ids: set[str],
) -> list[NextEvidenceRequest]:
    pending_ids = {
        request.fact_id
        for decision in decisions.values()
        for request in decision.pending_information
    }
    pending_ids.update(review_requested_ids)
    return [
        request
        for fact_id, request in request_by_id.items()
        if fact_id in pending_ids
    ]


def allowed_route(
    *,
    settings: EpisodeSettings,
    dirty_ids: set[str],
    decisions: Mapping[str, TrialDecision],
    pending: Sequence[NextEvidenceRequest],
    action_count: int,
    review_count: int,
    review_trial_ids: Sequence[str],
    forced_stop: PatientScreeningStopReason | None,
) -> tuple[list[CoordinatorRoute], list[str]]:
    """Return the single safe route and the identifiers it must receive."""

    if dirty_ids or not decisions:
        return [CoordinatorRoute.MATCHER_JUDGE], sorted(dirty_ids)
    if forced_stop is not None:
        return [CoordinatorRoute.FINISH], []
    if review_trial_ids and review_count < settings.max_selective_reviews:
        return [CoordinatorRoute.SELECTIVE_REVIEWER], [sorted(review_trial_ids)[0]]
    if pending and action_count < settings.max_external_actions:
        return [CoordinatorRoute.NEXT_EVIDENCE], [item.fact_id for item in pending]
    return [CoordinatorRoute.FINISH], []


def aggregate_screening_trial(
    *,
    trial: ScreeningTrial,
    assessments: Mapping[str, CriterionAssessment],
    evidence_requests: Sequence[NextEvidenceRequest],
    patient_state: PatientState,
) -> TrialDecision:
    """Aggregate criterion judgments without a model call."""

    missing_ids = {
        fact_id
        for item in assessments.values()
        for fact_id in item.missing_information_ids
    }
    pending = [item for item in evidence_requests if item.fact_id in missing_ids]
    decision = aggregate_trial_decision(
        trial_id=trial.trial_id,
        criteria=trial.criteria,
        assessments=list(assessments.values()),
        pending_information=pending,
        available_evidence_ids=[item.evidence_id for item in patient_state.facts],
        eligibility_logic=trial.eligibility_logic,
    )
    if trial.protocol_logic_supported:
        return decision
    return decision.model_copy(
        update={
            "candidate_status": CandidateStatus.RETAIN,
            "confirmation_status": ConfirmationStatus.NOT_CONFIRMED,
            "pending_information": pending,
            "next_action": AgentAction(
                action=NextAction.NONE,
                reason=(
                    "시험 조건 원문의 일부를 현재 구조로 안전하게 계산할 수 없어 "
                    "참가 여부를 확정하지 않았다."
                ),
            ),
            "review_required": False,
            "review_reasons": [],
            "logic_evaluation": None,
        }
    )


def no_action_decision(reason: str) -> AcquisitionDecision:
    return AcquisitionDecision(
        policy_id=AcquisitionPolicyId.PATIENT_ADAPTIVE,
        action_status=ActionStatus.DEFERRED,
        selection_reason=reason,
        decision_trace=DecisionTrace(
            considered_option_ids=[],
            removed_options=[],
            applied_ordering_rule=[],
        ),
    )


def attach_action_to_decisions(
    decisions: dict[str, TrialDecision],
    action: AgentAction,
    criterion_to_trial: Mapping[str, str],
) -> None:
    affected_trials = {
        criterion_to_trial[item] for item in action.related_criterion_ids
    }
    for trial_id in affected_trials:
        decisions[trial_id] = decisions[trial_id].model_copy(
            update={"next_action": action}
        )


def natural_stop_reason(
    *,
    settings: EpisodeSettings,
    decisions: Mapping[str, TrialDecision],
    pending: Sequence[NextEvidenceRequest],
    action_count: int,
) -> PatientScreeningStopReason:
    resolved = all(
        item.confirmation_status
        in {ConfirmationStatus.CONFIRMED, ConfirmationStatus.INELIGIBLE}
        for item in decisions.values()
    )
    if resolved:
        return PatientScreeningStopReason.ALL_TRIALS_RESOLVED
    if any(item.review_required for item in decisions.values()):
        return PatientScreeningStopReason.HUMAN_REVIEW
    if pending and action_count >= settings.max_external_actions:
        return PatientScreeningStopReason.ACTION_LIMIT
    return PatientScreeningStopReason.NO_PENDING_INFORMATION


def build_screening_result(
    *,
    case: PatientScreeningCase,
    patient_state: PatientState,
    decisions: Mapping[str, TrialDecision],
    history: list[PatientScreeningSnapshot],
    actions: list[PatientScreeningActionRecord],
    reviews: list[ReviewDecision],
    planned_action: AgentAction | None,
    acquisition_decision: AcquisitionDecision,
    profile: PatientBurdenProfile,
    view: InteractivePolicyView,
    catalog: Mapping[str, Sequence[AcquisitionOption]],
    revealed_fact_ids: set[str],
    selected_options: Sequence[AcquisitionOption],
    stop_reason: PatientScreeningStopReason,
    cycles: int,
) -> PatientScreeningResult:
    """Build guidance and the replayable workflow result from the same state."""

    snapshot = interactive_snapshot(patient_state, decisions)
    if stop_reason not in {
        PatientScreeningStopReason.AWAITING_PATIENT_CHOICE,
        PatientScreeningStopReason.AWAITING_CLINICIAN_AUTHORIZATION,
    }:
        acquisition_decision = select_acquisition_option(
            view=view,
            snapshot=snapshot,
            revealed_fact_ids=frozenset(revealed_fact_ids),
            catalog=catalog,
            profile=profile,
            policy_id=AcquisitionPolicyId.PATIENT_ADAPTIVE,
            selected_options=selected_options,
        )
    guidance = build_guidance_output(
        case=case,
        view=view,
        snapshot=snapshot,
        profile=profile,
        decision=acquisition_decision,
        catalog=catalog,
        revealed_fact_ids=frozenset(revealed_fact_ids),
        stop_reason=stop_reason.value,
    )
    if planned_action is not None and planned_action.message:
        guidance = guidance.model_copy(
            update={
                "patient_message": guidance.patient_message.model_copy(
                    update={"request_message": planned_action.message}
                )
            }
        )
    return PatientScreeningResult(
        case_id=case.case_id,
        stop_reason=stop_reason,
        final_patient_state=patient_state,
        final_decisions=sorted(decisions.values(), key=lambda item: item.trial_id),
        decision_history=history,
        action_history=actions,
        review_history=reviews,
        ineligible_boundary_differences=build_ineligible_boundary_differences(
            patient_state=patient_state,
            decisions=list(decisions.values()),
            criteria_by_id={
                criterion.criterion_id: criterion
                for trial in case.trials
                for criterion in trial.criteria
            },
        ),
        trial_reconsideration_summaries=build_trial_reconsideration_summaries(
            patient_state=patient_state,
            decisions=list(decisions.values()),
            trials=case.trials,
        ),
        planned_action=planned_action,
        guidance=guidance,
        cycles=cycles,
    )
