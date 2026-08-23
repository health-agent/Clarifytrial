"""Shared criterion-assessment step used by single- and multi-trial workflows."""

from __future__ import annotations

from collections.abc import Sequence

from ..agents import MatcherJudgeAgent
from ..contracts import (
    CriterionAssessment,
    EvidenceSufficiency,
    NextEvidenceRequest,
    PatientState,
    ReviewFlag,
    TrialCriterion,
)
from ..mechanical_checks import evaluate_criterion
from ..trace import TraceRecorder


class TrialAssessmentProtocolError(RuntimeError):
    """The matching role referred to data outside its supplied input."""


def assess_criteria_bundle(
    *,
    case_id: str,
    criteria: Sequence[TrialCriterion],
    patient_state: PatientState,
    evidence_requests: Sequence[NextEvidenceRequest],
    matcher_judge: MatcherJudgeAgent,
    trace: TraceRecorder,
    cycle: int,
) -> list[CriterionAssessment]:
    """Check and judge one or more trials in a single model call.

    The criteria may span trials.  Stable trial and criterion identifiers keep
    the response separable after the call, while deterministic checks retain
    authority over configured numeric and temporal rules.
    """

    if not criteria:
        raise ValueError("at least one criterion is required")
    criterion_ids = [item.criterion_id for item in criteria]
    if len(criterion_ids) != len(set(criterion_ids)):
        raise ValueError("criteria must not repeat criterion_id")
    trial_ids = sorted({item.trial_id for item in criteria})

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
            "trial_id": trial_ids[0] if len(trial_ids) == 1 else "multiple_trials",
            "trial_ids": trial_ids,
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
    mechanical_corrections = []
    for assessment in response.assessments:
        mechanical = mechanical_by_id[assessment.criterion_id]
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
        if mechanical.configured:
            related_missing_ids = sorted(
                request.fact_id
                for request in relevant_requests
                if assessment.criterion_id in request.related_criterion_ids
            )
            corrected_missing_ids = (
                []
                if mechanical.evidence_sufficiency is EvidenceSufficiency.SUFFICIENT
                else related_missing_ids
            )
            corrected_flags = [
                item
                for item in assessment.review_flags
                if item is not ReviewFlag.CODE_MODEL_MISMATCH
            ]
            differs = (
                assessment.clinical_status is not mechanical.clinical_status
                or assessment.evidence_sufficiency
                is not mechanical.evidence_sufficiency
                or assessment.evidence_ids != mechanical.evidence_ids
                or assessment.missing_information_ids != corrected_missing_ids
            )
            if differs:
                mechanical_corrections.append(
                    {
                        "criterion_id": assessment.criterion_id,
                        "model": {
                            "clinical_status": assessment.clinical_status.value,
                            "evidence_sufficiency": (
                                assessment.evidence_sufficiency.value
                            ),
                            "evidence_ids": assessment.evidence_ids,
                            "missing_information_ids": (
                                assessment.missing_information_ids
                            ),
                        },
                        "applied": {
                            "clinical_status": mechanical.clinical_status.value,
                            "evidence_sufficiency": (
                                mechanical.evidence_sufficiency.value
                            ),
                            "evidence_ids": mechanical.evidence_ids,
                            "missing_information_ids": corrected_missing_ids,
                        },
                    }
                )
            assessment = assessment.model_copy(
                update={
                    "clinical_status": mechanical.clinical_status,
                    "evidence_sufficiency": mechanical.evidence_sufficiency,
                    "evidence_ids": mechanical.evidence_ids,
                    "missing_information_ids": corrected_missing_ids,
                    "rationale": (
                        "구조화된 수치·단위·날짜·출처 검사의 코드 결과를 적용했다."
                    ),
                    "review_flags": corrected_flags,
                }
            )
        validated.append(assessment)
    if mechanical_corrections:
        trace.record(
            cycle=cycle,
            actor="mechanical_checks",
            event="model_assessments_replaced",
            input_refs=[
                str(item["criterion_id"]) for item in mechanical_corrections
            ],
            output={"corrections": mechanical_corrections},
        )
    return validated


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
    """Compatibility wrapper for a single supplied trial."""

    if any(item.trial_id != trial_id for item in criteria):
        raise ValueError("every criterion must belong to trial_id")
    return assess_criteria_bundle(
        case_id=case_id,
        criteria=criteria,
        patient_state=patient_state,
        evidence_requests=evidence_requests,
        matcher_judge=matcher_judge,
        trace=trace,
        cycle=cycle,
    )
