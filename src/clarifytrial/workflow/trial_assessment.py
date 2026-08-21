"""Shared criterion-assessment step used by single- and multi-trial workflows."""

from __future__ import annotations

from collections.abc import Sequence

from ..agents import MatcherJudgeAgent
from ..contracts import (
    CriterionAssessment,
    NextEvidenceRequest,
    PatientState,
    ReviewFlag,
    TrialCriterion,
)
from ..mechanical_checks import evaluate_criterion
from ..trace import TraceRecorder


class TrialAssessmentProtocolError(RuntimeError):
    """The matching role referred to data outside its supplied input."""


def assess_trial_criteria(
    *,
    case_id: str,
    trial_id: str,
    criteria: Sequence[TrialCriterion],
    patient_state: PatientState,
    evidence_requests: Sequence[NextEvidenceRequest],
    matcher_judge: MatcherJudgeAgent,
    trace: TraceRecorder,
    cycle: int,
) -> list[CriterionAssessment]:
    """Check structured values, call the matcher, and validate every identifier.

    This function is intentionally complete: both workflow runners use the
    same numeric checks, model payload, identifier checks, and mismatch flag.
    """

    if not criteria:
        raise ValueError("at least one criterion is required")
    criterion_ids = [item.criterion_id for item in criteria]
    if len(criterion_ids) != len(set(criterion_ids)):
        raise ValueError("criteria must not repeat criterion_id")
    if any(item.trial_id != trial_id for item in criteria):
        raise ValueError("every criterion must belong to trial_id")

    mechanical_by_id = {
        criterion.criterion_id: evaluate_criterion(criterion, patient_state)
        for criterion in criteria
    }
    evidence_ids = [item.evidence_id for item in patient_state.facts]
    trace.record(
        cycle=cycle,
        actor="mechanical_checks",
        event="criterion_checks_completed",
        input_refs=[*criterion_ids, *evidence_ids],
        output={
            criterion_id: result.model_dump(mode="json")
            for criterion_id, result in mechanical_by_id.items()
        },
    )

    criterion_id_set = set(criterion_ids)
    relevant_requests = [
        request
        for request in evidence_requests
        if set(request.related_criterion_ids) & criterion_id_set
    ]
    response = matcher_judge.run(
        {
            "case_id": case_id,
            "trial_id": trial_id,
            "as_of": patient_state.as_of.isoformat(),
            "criteria": [item.model_dump(mode="json") for item in criteria],
            "patient_facts": [
                item.model_dump(mode="json") for item in patient_state.facts
            ],
            "evidence_requests": [
                item.model_dump(mode="json") for item in relevant_requests
            ],
            "mechanical_checks": {
                criterion_id: result.model_dump(mode="json")
                for criterion_id, result in mechanical_by_id.items()
            },
        },
        trace=trace,
        cycle=cycle,
        input_refs=[*criterion_ids, *evidence_ids],
    ).output

    returned_ids = {item.criterion_id for item in response.assessments}
    if returned_ids != criterion_id_set:
        raise TrialAssessmentProtocolError(
            "matcher_judge must return exactly the requested criteria"
        )
    request_ids = {item.fact_id for item in evidence_requests}
    validated = []
    for assessment in response.assessments:
        mechanical = mechanical_by_id[assessment.criterion_id]
        if mechanical.configured and (
            assessment.clinical_status is not mechanical.clinical_status
            or assessment.evidence_sufficiency
            is not mechanical.evidence_sufficiency
        ):
            flags = list(assessment.review_flags)
            if ReviewFlag.CODE_MODEL_MISMATCH not in flags:
                flags.append(ReviewFlag.CODE_MODEL_MISMATCH)
            assessment = assessment.model_copy(update={"review_flags": flags})
        unknown_missing = set(assessment.missing_information_ids) - request_ids
        if unknown_missing:
            raise TrialAssessmentProtocolError(
                "matcher_judge invented missing-information identifiers: "
                + ", ".join(sorted(unknown_missing))
            )
        unknown_evidence = set(assessment.evidence_ids) - set(evidence_ids)
        if unknown_evidence:
            raise TrialAssessmentProtocolError(
                "matcher_judge invented patient-evidence identifiers: "
                + ", ".join(sorted(unknown_evidence))
            )
        validated.append(assessment)
    return validated
