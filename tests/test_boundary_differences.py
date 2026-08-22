from datetime import datetime, timezone

from clarifytrial.contracts import (
    AgentAction,
    CandidateStatus,
    ClinicalStatus,
    ComparisonOperator,
    ConfirmationStatus,
    CriterionAssessment,
    CriterionKind,
    EvidenceFact,
    EvidenceSourceType,
    EvidenceSufficiency,
    NextAction,
    NumericConstraint,
    PatientState,
    TrialCriterion,
    TrialDecision,
    VerificationStatus,
)
from clarifytrial.reporting import build_ineligible_boundary_differences


def _decision(assessment: CriterionAssessment) -> TrialDecision:
    return TrialDecision(
        trial_id="TRIAL-1",
        candidate_status=CandidateStatus.REMOVE,
        confirmation_status=ConfirmationStatus.INELIGIBLE,
        criterion_assessments=[assessment],
        next_action=AgentAction(action=NextAction.NONE, reason="판정이 끝났다."),
    )


def _assessment(*, sufficient: bool = True) -> CriterionAssessment:
    return CriterionAssessment(
        criterion_id="criterion-1",
        criterion_source_location="protocol#criterion-1",
        clinical_status=ClinicalStatus.VIOLATES,
        evidence_sufficiency=(
            EvidenceSufficiency.SUFFICIENT
            if sufficient
            else EvidenceSufficiency.INSUFFICIENT
        ),
        evidence_ids=["evidence-1"],
        rationale="구조화된 값을 비교했다.",
    )


def _criterion(unit: str = "days") -> TrialCriterion:
    return TrialCriterion(
        criterion_id="criterion-1",
        trial_id="TRIAL-1",
        kind=CriterionKind.INCLUSION,
        statement="치료를 안정적으로 유지한 기간이 100일 이상이어야 한다.",
        source_location="protocol#criterion-1",
        numeric_constraint=NumericConstraint(
            concept="stable_treatment_days",
            operator=ComparisonOperator.GTE,
            threshold=100,
            unit=unit,
        ),
    )


def _state(unit: str = "days") -> PatientState:
    return PatientState(
        patient_id="SYNTHETIC-1",
        as_of=datetime(2026, 8, 22, tzinfo=timezone.utc),
        facts=[
            EvidenceFact(
                evidence_id="evidence-1",
                statement="합성 환자의 치료 유지 기간은 92일이다.",
                source_type=EvidenceSourceType.SYNTHETIC_CASE,
                source_location="synthetic#stable-days",
                verification_status=VerificationStatus.VERIFIED,
                concept="stable_treatment_days",
                value=92,
                unit=unit,
            )
        ],
    )


def test_numeric_or_temporal_violation_reports_plain_cutoff_difference() -> None:
    result = build_ineligible_boundary_differences(
        patient_state=_state(),
        decisions=[_decision(_assessment())],
        criteria_by_id={"criterion-1": _criterion()},
    )

    assert len(result) == 1
    assert result[0].difference_from_threshold == -8
    assert result[0].absolute_difference == 8
    assert result[0].explanation == (
        "선정 조건은 100 days 이상입니다. 현재 값은 92 days이며, "
        "기준보다 8 days 낮습니다."
    )


def test_boolean_and_insufficient_evidence_do_not_get_distance_claims() -> None:
    boolean = build_ineligible_boundary_differences(
        patient_state=_state("bool"),
        decisions=[_decision(_assessment())],
        criteria_by_id={"criterion-1": _criterion("bool")},
    )
    insufficient = build_ineligible_boundary_differences(
        patient_state=_state(),
        decisions=[_decision(_assessment(sufficient=False))],
        criteria_by_id={"criterion-1": _criterion()},
    )
    unit_mismatch = build_ineligible_boundary_differences(
        patient_state=_state("days"),
        decisions=[_decision(_assessment())],
        criteria_by_id={"criterion-1": _criterion("hours")},
    )

    assert boolean == []
    assert insufficient == []
    assert unit_mismatch == []
