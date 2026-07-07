"""Patient Profile Understanding Agent.

Responsibility: build and maintain the normalized ``PatientProfile`` from
patient free text, and normalize free-text clarification answers into
structured variables BEFORE any rule update is applied (locked invariant:
raw free text never flows directly into rule evaluation).
"""

from __future__ import annotations

from typing import Any

from models import AnswerUpdate, FollowUpQuestion, PatientProfile


def extract_patient_profile_from_summary(
    patient_id: str, summary_text: str
) -> PatientProfile:
    """Extract a structured profile from a natural-language case summary.

    Input contract for professor-style synthetic patient summaries: the
    summary is a patient INPUT only, never eligibility ground truth.

    This stub does NOT call an LLM. It returns a minimal placeholder
    profile that keeps the raw text in ``free_text_notes`` so the contract
    is callable and testable.

    TODO: replace the placeholder with LLM-based extraction of
    demographics, conditions, medications, and normalized variables from
    the summary text.
    """
    return PatientProfile(patient_id=patient_id, free_text_notes=summary_text)


def build_patient_profile(patient_id: str, free_text: str) -> PatientProfile:
    """Extract a structured profile from patient free text.

    TODO: LLM call to extract demographics, conditions, medications and
    normalized variables (units, coded values) from the narrative.
    """
    raise NotImplementedError("LLM-based profile extraction not implemented in skeleton")


def normalize_clarification_answer(
    question: FollowUpQuestion, raw_answer_text: str
) -> AnswerUpdate:
    """Normalize a free-text clarification answer into a typed value.

    TODO: LLM call to map raw_answer_text onto
    question.expected_answer_type / allowed_values_or_schema, producing a
    normalized_value that the Eligibility State Tracker can apply.
    """
    raise NotImplementedError("Answer normalization not implemented in skeleton")


def apply_normalized_value(
    profile: PatientProfile, target_profile_field: str, value: Any
) -> PatientProfile:
    """Write a normalized value into the profile's variables.

    TODO: field routing / validation against the profile schema.
    """
    raise NotImplementedError("Profile update not implemented in skeleton")
