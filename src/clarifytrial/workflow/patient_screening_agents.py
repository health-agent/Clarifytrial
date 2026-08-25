"""Validated model-call boundaries used by the patient screening workflow."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from ..agents import NextEvidenceAgent, ReviewDecision, ReviewOutcome
from ..contracts import AgentAction, NextEvidenceRequest, PatientState, TrialDecision
from ..interactive.burden_contracts import AcquisitionDecision, AcquisitionOption
from ..trace import TraceRecorder
from .episode import WorkflowProtocolError
from .patient_screening_contracts import (
    PatientScreeningActionRecord,
    ScreeningTrial,
)


def write_information_request(
    *,
    next_evidence: NextEvidenceAgent,
    case_id: str,
    request: NextEvidenceRequest,
    selected: AcquisitionOption,
    acquisition_decision: AcquisitionDecision,
    attempted: Sequence[PatientScreeningActionRecord],
    trace: TraceRecorder,
    cycle: int,
) -> AgentAction:
    """Let the model write a message without changing the code-selected action."""

    required_action = {
        "action": selected.action.value,
        "target_fact_id": selected.fact_id,
        "related_criterion_ids": request.related_criterion_ids,
    }
    action = next_evidence.run(
        {
            "case_id": case_id,
            "required_action": required_action,
            "selected_acquisition_option": selected.model_dump(mode="json"),
            "selection_reason": acquisition_decision.selection_reason,
            "pending_information": [request.model_dump(mode="json")],
            "attempted_actions": [
                {
                    "action": item.agent_action.action.value,
                    "target_fact_id": item.agent_action.target_fact_id,
                    "status": item.tool_result.status.value,
                }
                for item in attempted
            ],
        },
        trace=trace,
        cycle=cycle,
        input_refs=[request.fact_id, selected.option_id],
    ).output
    if (
        action.action.value != required_action["action"]
        or action.target_fact_id != required_action["target_fact_id"]
        or action.related_criterion_ids != required_action["related_criterion_ids"]
    ):
        raise WorkflowProtocolError(
            "next_evidence may write the request message but cannot change the "
            "fact, acquisition path, or related criteria chosen by code"
        )
    return action


def build_review_payload(
    *,
    case_id: str,
    trial: ScreeningTrial,
    decision: TrialDecision,
    patient_state: PatientState,
) -> dict[str, Any]:
    cited_ids = {
        evidence_id
        for item in decision.criterion_assessments
        for evidence_id in item.evidence_ids
    }
    return {
        "case_id": case_id,
        "conclusion_id": f"trial:{trial.trial_id}",
        "candidate_status": decision.candidate_status.value,
        "confirmation_status": decision.confirmation_status.value,
        "review_reasons": [item.value for item in decision.review_reasons],
        "criterion_assessments": [
            item.model_dump(mode="json") for item in decision.criterion_assessments
        ],
        "criteria": [item.model_dump(mode="json") for item in trial.criteria],
        "eligibility_logic": (
            None
            if trial.eligibility_logic is None
            else trial.eligibility_logic.model_dump(mode="json")
        ),
        "logic_evaluation": (
            None
            if decision.logic_evaluation is None
            else decision.logic_evaluation.model_dump(mode="json")
        ),
        "patient_facts": [
            item.model_dump(mode="json")
            for item in patient_state.facts
            if item.evidence_id in cited_ids
        ],
        "as_of": patient_state.as_of.isoformat(),
    }


def validate_review(
    review: ReviewDecision,
    *,
    trial_id: str,
    known_criterion_ids: set[str],
    known_fact_ids: set[str],
    known_patient_evidence_ids: set[str],
    known_trial_evidence_ids: set[str],
) -> None:
    """Reject review output that escapes the supplied trial and fact IDs."""

    if review.conclusion_id != f"trial:{trial_id}":
        raise WorkflowProtocolError(
            "selective_reviewer returned an unknown conclusion_id"
        )
    if set(review.affected_condition_ids) - known_criterion_ids:
        raise WorkflowProtocolError(
            "selective_reviewer returned unknown condition identifiers"
        )
    if set(review.missing_fact_ids) - known_fact_ids:
        raise WorkflowProtocolError(
            "selective_reviewer returned unknown missing fact identifiers"
        )
    if set(review.patient_evidence_ids) - known_patient_evidence_ids:
        raise WorkflowProtocolError(
            "selective_reviewer returned unknown patient evidence identifiers"
        )
    if set(review.trial_evidence_ids) - known_trial_evidence_ids:
        raise WorkflowProtocolError(
            "selective_reviewer returned unknown trial evidence identifiers"
        )
    if review.decision is ReviewOutcome.REJUDGE and not review.affected_condition_ids:
        raise WorkflowProtocolError("rejudge requires an affected condition")
    if (
        review.decision is ReviewOutcome.REQUEST_MORE_EVIDENCE
        and not review.missing_fact_ids
    ):
        raise WorkflowProtocolError("request_more_evidence requires a missing fact")
