from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from clarifytrial.contracts import (
    ClinicalStatus,
    ComparisonOperator,
    CriterionKind,
    EvidenceFact,
    EvidenceRequirement,
    EvidenceSourceType,
    EvidenceSufficiency,
    NumericConstraint,
    PatientState,
    TrialCriterion,
    VerificationStatus,
)
from clarifytrial.mechanical_checks import MechanicalIssueCode, evaluate_criterion


AS_OF = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)


def _criterion(
    *,
    concept: str = "platelet_count",
    operator: ComparisonOperator = ComparisonOperator.GTE,
    threshold: float = 100,
    unit: str = "10^9/L",
) -> TrialCriterion:
    return TrialCriterion(
        criterion_id="platelet-minimum",
        trial_id="NCT-SYNTHETIC-001",
        kind=CriterionKind.INCLUSION,
        statement="Platelets must be at least 100 x10^9/L within 14 days.",
        source_location="synthetic-protocol#inclusion-4",
        numeric_constraint=NumericConstraint(
            concept=concept,
            operator=operator,
            threshold=threshold,
            unit=unit,
        ),
        evidence_requirement=EvidenceRequirement(
            max_age_days=14,
            allowed_source_types=[
                EvidenceSourceType.MEDICAL_RECORD,
                EvidenceSourceType.OFFICIAL_VERIFICATION,
            ],
            allowed_verification_statuses=[VerificationStatus.VERIFIED],
        ),
    )


def _fact(
    *,
    evidence_id: str,
    value: float,
    event_date: date,
    unit: str = "10^9/L",
    source_type: EvidenceSourceType = EvidenceSourceType.OFFICIAL_VERIFICATION,
    verification_status: VerificationStatus = VerificationStatus.VERIFIED,
    concept: str = "platelet_count",
    recorded_date: date | None = None,
) -> EvidenceFact:
    return EvidenceFact(
        evidence_id=evidence_id,
        statement="Synthetic platelet result.",
        source_type=source_type,
        source_location=f"synthetic-record#{evidence_id}",
        event_date=event_date,
        recorded_date=recorded_date or event_date,
        verification_status=verification_status,
        concept=concept,
        value=value,
        unit=unit,
    )


def _state(*facts: EvidenceFact) -> PatientState:
    return PatientState(
        patient_id="synthetic-patient-001",
        as_of=AS_OF,
        facts=list(facts),
    )


def test_old_qualifying_value_supports_but_cannot_confirm() -> None:
    result = evaluate_criterion(
        _criterion(),
        _state(
            _fact(
                evidence_id="old-platelets",
                value=132,
                event_date=date(2026, 5, 20),
                source_type=EvidenceSourceType.MEDICAL_RECORD,
            )
        ),
    )

    assert result.configured is True
    assert result.clinical_status is ClinicalStatus.SUPPORTS
    assert result.evidence_sufficiency is EvidenceSufficiency.INSUFFICIENT
    assert result.evidence_ids == ["old-platelets"]
    assert result.issue_codes == [MechanicalIssueCode.EVIDENCE_TOO_OLD]


def test_recent_official_qualifying_value_supports_and_confirms() -> None:
    result = evaluate_criterion(
        _criterion(),
        _state(
            _fact(
                evidence_id="recent-official-platelets",
                value=126,
                event_date=date(2026, 8, 18),
            )
        ),
    )

    assert result.clinical_status is ClinicalStatus.SUPPORTS
    assert result.evidence_sufficiency is EvidenceSufficiency.SUFFICIENT
    assert result.issue_codes == []


def test_record_added_after_the_decision_time_cannot_confirm() -> None:
    result = evaluate_criterion(
        _criterion(),
        _state(
            _fact(
                evidence_id="late-record-entry",
                value=126,
                event_date=date(2026, 8, 18),
                recorded_date=date(2026, 8, 21),
            )
        ),
    )

    assert result.clinical_status is ClinicalStatus.SUPPORTS
    assert result.evidence_sufficiency is EvidenceSufficiency.INSUFFICIENT
    assert result.issue_codes == [MechanicalIssueCode.FUTURE_RECORDED_DATE]


def test_same_day_verified_results_on_opposite_sides_are_conflicting() -> None:
    result = evaluate_criterion(
        _criterion(),
        _state(
            _fact(
                evidence_id="same-day-low",
                value=82,
                event_date=date(2026, 8, 18),
            ),
            _fact(
                evidence_id="same-day-high",
                value=126,
                event_date=date(2026, 8, 18),
            ),
        ),
    )

    assert result.clinical_status is ClinicalStatus.UNKNOWN
    assert result.evidence_sufficiency is EvidenceSufficiency.CONFLICTING
    assert result.evidence_ids == ["same-day-high", "same-day-low"]
    assert result.issue_codes == [MechanicalIssueCode.EVIDENCE_CONFLICT]


def test_concept_label_formatting_does_not_hide_matching_evidence() -> None:
    result = evaluate_criterion(
        _criterion(),
        _state(
            _fact(
                evidence_id="recent-platelets",
                value=126,
                event_date=date(2026, 8, 18),
                concept="Platelet Count",
            )
        ),
    )

    assert result.clinical_status is ClinicalStatus.SUPPORTS
    assert result.evidence_sufficiency is EvidenceSufficiency.SUFFICIENT
    assert result.evidence_ids == ["recent-platelets"]


def test_recent_numeric_threshold_failure_violates_with_sufficient_evidence() -> None:
    result = evaluate_criterion(
        _criterion(),
        _state(
            _fact(
                evidence_id="recent-low-platelets",
                value=82,
                event_date=date(2026, 8, 18),
            )
        ),
    )

    assert result.clinical_status is ClinicalStatus.VIOLATES
    assert result.evidence_sufficiency is EvidenceSufficiency.SUFFICIENT


@pytest.mark.parametrize(
    ("value", "expected_status"),
    [
        (126, ClinicalStatus.VIOLATES),
        (82, ClinicalStatus.SUPPORTS),
    ],
)
def test_exclusion_status_is_reported_from_eligibility_direction(
    value: float,
    expected_status: ClinicalStatus,
) -> None:
    exclusion = _criterion().model_copy(
        update={
            "kind": CriterionKind.EXCLUSION,
            "statement": "Exclude patients with platelets at least 100 x10^9/L.",
        }
    )

    result = evaluate_criterion(
        exclusion,
        _state(
            _fact(
                evidence_id=f"exclusion-platelets-{value}",
                value=value,
                event_date=date(2026, 8, 18),
            )
        ),
    )

    assert result.clinical_status is expected_status
    assert result.evidence_sufficiency is EvidenceSufficiency.SUFFICIENT


@pytest.mark.parametrize(
    ("operator", "value", "threshold"),
    [
        (ComparisonOperator.GT, 101, 100),
        (ComparisonOperator.GTE, 100, 100),
        (ComparisonOperator.LT, 99, 100),
        (ComparisonOperator.LTE, 100, 100),
        (ComparisonOperator.EQ, 100, 100),
    ],
)
def test_each_supported_comparison_operator_can_satisfy_a_constraint(
    operator: ComparisonOperator,
    value: float,
    threshold: float,
) -> None:
    result = evaluate_criterion(
        _criterion(operator=operator, threshold=threshold),
        _state(
            _fact(
                evidence_id=f"result-{operator.value}",
                value=value,
                event_date=date(2026, 8, 18),
            )
        ),
    )

    assert result.clinical_status is ClinicalStatus.SUPPORTS


def test_equivalent_free_text_or_other_unit_is_not_inferred() -> None:
    result = evaluate_criterion(
        _criterion(unit="10^9/L"),
        _state(
            _fact(
                evidence_id="different-unit",
                value=126,
                unit="10^3/uL",
                event_date=date(2026, 8, 18),
            )
        ),
    )

    assert result.clinical_status is ClinicalStatus.UNKNOWN
    assert result.evidence_sufficiency is EvidenceSufficiency.INSUFFICIENT
    assert result.issue_codes == [MechanicalIssueCode.UNIT_MISMATCH]


def test_equivalent_unit_notation_is_used_without_value_conversion() -> None:
    result = evaluate_criterion(
        _criterion(unit="10^9/L"),
        _state(
            _fact(
                evidence_id="equivalent-unit-notation",
                value=126,
                unit="× 10⁹ / litre",
                event_date=date(2026, 8, 18),
            )
        ),
    )

    assert result.clinical_status is ClinicalStatus.SUPPORTS
    assert result.evidence_sufficiency is EvidenceSufficiency.SUFFICIENT
    assert result.issue_codes == []


def test_age_in_months_is_compared_with_an_age_limit_in_years() -> None:
    result = evaluate_criterion(
        _criterion(
            concept="age",
            operator=ComparisonOperator.GTE,
            threshold=18,
            unit="years",
        ),
        _state(
            _fact(
                evidence_id="infant-age",
                concept="age",
                value=3,
                unit="month-old",
                event_date=date(2026, 8, 20),
            )
        ),
    )

    assert result.clinical_status is ClinicalStatus.VIOLATES
    assert result.evidence_sufficiency is EvidenceSufficiency.SUFFICIENT
    assert result.issue_codes == []


def test_disallowed_source_and_verification_are_reported_separately() -> None:
    result = evaluate_criterion(
        _criterion(),
        _state(
            _fact(
                evidence_id="patient-reported-value",
                value=126,
                event_date=date(2026, 8, 18),
                source_type=EvidenceSourceType.PATIENT_REPORT,
                verification_status=VerificationStatus.REPORTED,
            )
        ),
    )

    assert result.clinical_status is ClinicalStatus.SUPPORTS
    assert result.evidence_sufficiency is EvidenceSufficiency.INSUFFICIENT
    assert result.issue_codes == [
        MechanicalIssueCode.SOURCE_NOT_ALLOWED,
        MechanicalIssueCode.VERIFICATION_NOT_ALLOWED,
    ]


def test_partial_structured_numeric_fact_is_rejected() -> None:
    with pytest.raises(ValidationError, match="must be provided together"):
        EvidenceFact(
            evidence_id="partial-value",
            statement="The prose contains a value, but the unit field is absent.",
            source_type=EvidenceSourceType.MEDICAL_RECORD,
            source_location="synthetic-record#partial",
            event_date=date(2026, 8, 18),
            verification_status=VerificationStatus.VERIFIED,
            concept="platelet_count",
            value=126,
        )


def test_free_text_alone_is_not_used_as_structured_numeric_evidence() -> None:
    prose_only = EvidenceFact(
        evidence_id="prose-only",
        statement="Platelets were 126 x10^9/L.",
        source_type=EvidenceSourceType.OFFICIAL_VERIFICATION,
        source_location="synthetic-record#prose",
        event_date=date(2026, 8, 18),
        verification_status=VerificationStatus.VERIFIED,
    )

    result = evaluate_criterion(_criterion(), _state(prose_only))

    assert result.clinical_status is ClinicalStatus.UNKNOWN
    assert result.issue_codes == [MechanicalIssueCode.NO_MATCHING_EVIDENCE]
