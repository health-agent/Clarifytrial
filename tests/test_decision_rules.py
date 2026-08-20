from __future__ import annotations

import pytest

from clarifytrial.contracts import (
    CandidateStatus,
    ClinicalStatus,
    ConfirmationStatus,
    CriterionAssessment,
    CriterionKind,
    EvidenceSufficiency,
    ReviewFlag,
    ReviewReason,
    TrialCriterion,
)
from clarifytrial.decision_rules import (
    aggregate_statuses,
    aggregate_trial_decision,
    requires_selective_review,
    select_review_reasons,
)


def criterion(
    criterion_id: str,
    *,
    required: bool = True,
    source_location: str | None = None,
) -> TrialCriterion:
    return TrialCriterion(
        criterion_id=criterion_id,
        trial_id="NCT-test",
        kind=CriterionKind.INCLUSION,
        statement=f"Synthetic criterion {criterion_id}",
        source_location=source_location or f"NCT-test:eligibility:{criterion_id}",
        required=required,
    )


def assessment(
    criterion_id: str,
    *,
    status: ClinicalStatus,
    sufficiency: EvidenceSufficiency,
    evidence_ids: list[str] | None = None,
    source_location: str | None = None,
    review_flags: list[ReviewFlag] | None = None,
) -> CriterionAssessment:
    return CriterionAssessment(
        criterion_id=criterion_id,
        criterion_source_location=(
            source_location or f"NCT-test:eligibility:{criterion_id}"
        ),
        clinical_status=status,
        evidence_sufficiency=sufficiency,
        evidence_ids=list(evidence_ids or []),
        missing_information_ids=(
            [f"missing-{criterion_id}"]
            if sufficiency is EvidenceSufficiency.INSUFFICIENT
            else []
        ),
        rationale="Synthetic rationale tied to the cited criterion.",
        review_flags=list(review_flags or []),
    )


def test_sufficient_clear_violation_removes_and_marks_ineligible() -> None:
    statuses = aggregate_statuses(
        [criterion("c-1")],
        [
            assessment(
                "c-1",
                status=ClinicalStatus.VIOLATES,
                sufficiency=EvidenceSufficiency.SUFFICIENT,
                evidence_ids=["e-1"],
            )
        ],
    )

    assert statuses == (CandidateStatus.REMOVE, ConfirmationStatus.INELIGIBLE)


@pytest.mark.parametrize(
    ("status", "sufficiency"),
    [
        (ClinicalStatus.VIOLATES, EvidenceSufficiency.INSUFFICIENT),
        (ClinicalStatus.SUPPORTS, EvidenceSufficiency.INSUFFICIENT),
        (ClinicalStatus.UNKNOWN, EvidenceSufficiency.INSUFFICIENT),
        (ClinicalStatus.NOT_APPLICABLE, EvidenceSufficiency.SUFFICIENT),
    ],
)
def test_incomplete_required_evidence_retains_without_confirmation(
    status: ClinicalStatus,
    sufficiency: EvidenceSufficiency,
) -> None:
    statuses = aggregate_statuses(
        [criterion("c-1")],
        [assessment("c-1", status=status, sufficiency=sufficiency)],
    )

    assert statuses == (
        CandidateStatus.RETAIN,
        ConfirmationStatus.NOT_CONFIRMED,
    )


def test_all_required_criteria_need_sufficient_support_for_confirmation() -> None:
    criteria = [criterion("c-1"), criterion("c-2")]
    assessments = [
        assessment(
            "c-1",
            status=ClinicalStatus.SUPPORTS,
            sufficiency=EvidenceSufficiency.SUFFICIENT,
            evidence_ids=["e-1"],
        ),
        assessment(
            "c-2",
            status=ClinicalStatus.SUPPORTS,
            sufficiency=EvidenceSufficiency.SUFFICIENT,
            evidence_ids=["e-2"],
        ),
    ]

    assert aggregate_statuses(criteria, assessments) == (
        CandidateStatus.RETAIN,
        ConfirmationStatus.CONFIRMED,
    )


def test_missing_required_assessment_is_not_a_negative_judgment() -> None:
    assert aggregate_statuses([criterion("c-1")], []) == (
        CandidateStatus.RETAIN,
        ConfirmationStatus.NOT_CONFIRMED,
    )


def test_conflicting_evidence_has_precedence_over_other_results() -> None:
    criteria = [criterion("c-1"), criterion("c-2")]
    assessments = [
        assessment(
            "c-1",
            status=ClinicalStatus.VIOLATES,
            sufficiency=EvidenceSufficiency.SUFFICIENT,
            evidence_ids=["e-1"],
        ),
        assessment(
            "c-2",
            status=ClinicalStatus.SUPPORTS,
            sufficiency=EvidenceSufficiency.CONFLICTING,
            evidence_ids=["e-2", "e-3"],
        ),
    ]

    assert aggregate_statuses(criteria, assessments) == (
        CandidateStatus.UNCERTAIN,
        ConfirmationStatus.UNCERTAIN,
    )


def test_optional_criterion_does_not_block_confirmation() -> None:
    criteria = [criterion("required"), criterion("optional", required=False)]
    assessments = [
        assessment(
            "required",
            status=ClinicalStatus.SUPPORTS,
            sufficiency=EvidenceSufficiency.SUFFICIENT,
            evidence_ids=["e-1"],
        ),
        assessment(
            "optional",
            status=ClinicalStatus.UNKNOWN,
            sufficiency=EvidenceSufficiency.INSUFFICIENT,
        ),
    ]

    assert aggregate_statuses(criteria, assessments) == (
        CandidateStatus.RETAIN,
        ConfirmationStatus.CONFIRMED,
    )


def test_ordinary_missing_information_does_not_call_reviewer() -> None:
    criteria = [criterion("c-1")]
    assessments = [
        assessment(
            "c-1",
            status=ClinicalStatus.UNKNOWN,
            sufficiency=EvidenceSufficiency.INSUFFICIENT,
        )
    ]

    assert not requires_selective_review(
        criteria=criteria,
        assessments=assessments,
        candidate_status=CandidateStatus.RETAIN,
        confirmation_status=ConfirmationStatus.NOT_CONFIRMED,
    )


def test_decisive_claim_without_evidence_selects_review() -> None:
    criteria = [criterion("c-1")]
    assessments = [
        assessment(
            "c-1",
            status=ClinicalStatus.VIOLATES,
            sufficiency=EvidenceSufficiency.SUFFICIENT,
        )
    ]

    reasons = select_review_reasons(
        criteria=criteria,
        assessments=assessments,
        candidate_status=CandidateStatus.REMOVE,
        confirmation_status=ConfirmationStatus.INELIGIBLE,
    )

    assert ReviewReason.MISSING_EVIDENCE in reasons
    assert ReviewReason.DECISIVE_RESULT_EVIDENCE_DEFECT in reasons


def test_structural_source_and_code_mismatches_select_review() -> None:
    criteria = [criterion("c-1")]
    assessments = [
        assessment(
            "c-1",
            status=ClinicalStatus.SUPPORTS,
            sufficiency=EvidenceSufficiency.SUFFICIENT,
            evidence_ids=["e-1"],
            source_location="wrong:location",
            review_flags=[ReviewFlag.CODE_MODEL_MISMATCH],
        )
    ]

    reasons = select_review_reasons(
        criteria=criteria,
        assessments=assessments,
        candidate_status=CandidateStatus.RETAIN,
        confirmation_status=ConfirmationStatus.CONFIRMED,
        available_evidence_ids={"e-1"},
    )

    assert reasons == [
        ReviewReason.EXPLICIT_FLAG,
        ReviewReason.CODE_MODEL_MISMATCH,
        ReviewReason.CRITERION_SOURCE_MISMATCH,
        ReviewReason.DECISIVE_RESULT_EVIDENCE_DEFECT,
    ]


def test_unknown_evidence_identifier_is_a_reviewable_defect() -> None:
    decision = aggregate_trial_decision(
        trial_id="NCT-test",
        criteria=[criterion("c-1")],
        assessments=[
            assessment(
                "c-1",
                status=ClinicalStatus.SUPPORTS,
                sufficiency=EvidenceSufficiency.SUFFICIENT,
                evidence_ids=["not-in-patient-state"],
            )
        ],
        available_evidence_ids={"e-1"},
    )

    assert decision.candidate_status is CandidateStatus.RETAIN
    assert decision.confirmation_status is ConfirmationStatus.CONFIRMED
    assert decision.review_required
    assert ReviewReason.MISSING_EVIDENCE in decision.review_reasons
    assert decision.criterion_assessments[0].criterion_source_location == (
        "NCT-test:eligibility:c-1"
    )


def test_unknown_assessment_criterion_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown criteria"):
        aggregate_statuses(
            [criterion("c-1")],
            [
                assessment(
                    "c-2",
                    status=ClinicalStatus.SUPPORTS,
                    sufficiency=EvidenceSufficiency.SUFFICIENT,
                    evidence_ids=["e-1"],
                )
            ],
        )
