from __future__ import annotations

from datetime import date, datetime, timezone

import pytest
from pydantic import ValidationError

from clarifytrial.contracts import (
    AgentAction,
    CandidateStatus,
    ClinicalStatus,
    ConfirmationStatus,
    CriterionAssessment,
    EvidenceFact,
    EvidenceSourceType,
    EvidenceSufficiency,
    NextAction,
    NextEvidenceRequest,
    PatientState,
    ReviewReason,
    TrialDecision,
    VerificationStatus,
)


def evidence(evidence_id: str = "e-1") -> EvidenceFact:
    return EvidenceFact(
        evidence_id=evidence_id,
        statement="ANC was 1,700/µL.",
        source_type=EvidenceSourceType.MEDICAL_RECORD,
        source_location="synthetic-record:lab-1",
        event_date=date(2026, 8, 10),
        recorded_date=date(2026, 8, 10),
        verification_status=VerificationStatus.VERIFIED,
    )


def assessment() -> CriterionAssessment:
    return CriterionAssessment(
        criterion_id="c-1",
        criterion_source_location="NCT-test:eligibility:3",
        clinical_status=ClinicalStatus.SUPPORTS,
        evidence_sufficiency=EvidenceSufficiency.SUFFICIENT,
        evidence_ids=["e-1"],
        rationale="The dated laboratory result meets the stated threshold.",
    )


def no_action() -> AgentAction:
    return AgentAction(action=NextAction.NONE, reason="No missing fact can change the result.")


def test_evidence_fact_round_trips_as_plain_json() -> None:
    payload = evidence().model_dump(mode="json")

    assert payload["evidence_id"] == "e-1"
    assert payload["source_type"] == "medical_record"
    assert payload["event_date"] == "2026-08-10"
    assert EvidenceFact.model_validate(payload) == evidence()


def test_patient_state_rejects_duplicate_evidence_identifiers() -> None:
    with pytest.raises(ValidationError, match="duplicate identifiers"):
        PatientState(
            patient_id="synthetic-patient-1",
            as_of=datetime(2026, 8, 20, tzinfo=timezone.utc),
            facts=[evidence(), evidence()],
        )


def test_assessment_keeps_both_patient_and_trial_references() -> None:
    item = assessment()

    assert item.evidence_ids == ["e-1"]
    assert item.criterion_source_location == "NCT-test:eligibility:3"


def test_contract_rejects_confidence_threshold_fields() -> None:
    payload = assessment().model_dump()
    payload["confidence"] = 0.91

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        CriterionAssessment.model_validate(payload)


def test_none_action_cannot_hide_a_fact_request() -> None:
    with pytest.raises(ValidationError, match="NONE cannot target"):
        AgentAction(
            action=NextAction.NONE,
            target_fact_id="missing-lab",
            reason="Invalid mixed state.",
        )


def test_external_action_names_one_fact_and_one_criterion() -> None:
    action = AgentAction(
        action=NextAction.REQUEST_VERIFICATION,
        target_fact_id="recent-anc",
        related_criterion_ids=["c-1"],
        reason="The protocol requires a result from the last 14 days.",
        message="Request the protocol-approved recent ANC result.",
    )

    assert action.action is NextAction.REQUEST_VERIFICATION
    assert action.target_fact_id == "recent-anc"


def test_patient_question_requires_visible_question_text() -> None:
    with pytest.raises(ValidationError, match="need a message"):
        AgentAction(
            action=NextAction.ASK_PATIENT,
            target_fact_id="current-medication",
            related_criterion_ids=["c-1"],
            reason="Medication history is incomplete.",
        )


def test_missing_evidence_request_excludes_none() -> None:
    with pytest.raises(ValidationError, match="NONE cannot obtain"):
        NextEvidenceRequest(
            fact_id="recent-anc",
            description="ANC result from the last 14 days",
            related_criterion_ids=["c-1"],
            acceptable_actions=[NextAction.NONE],
            reason="A recent result is required for confirmation.",
        )


def test_trial_decision_requires_reason_when_review_is_selected() -> None:
    with pytest.raises(ValidationError, match="at least one review reason"):
        TrialDecision(
            trial_id="NCT-test",
            candidate_status=CandidateStatus.RETAIN,
            confirmation_status=ConfirmationStatus.CONFIRMED,
            criterion_assessments=[assessment()],
            next_action=no_action(),
            review_required=True,
        )


def test_trial_decision_serializes_review_reason() -> None:
    decision = TrialDecision(
        trial_id="NCT-test",
        candidate_status=CandidateStatus.REMOVE,
        confirmation_status=ConfirmationStatus.INELIGIBLE,
        criterion_assessments=[assessment()],
        next_action=no_action(),
        review_required=True,
        review_reasons=[ReviewReason.CODE_MODEL_MISMATCH],
    )

    assert decision.model_dump(mode="json")["review_reasons"] == [
        "code_model_mismatch"
    ]
