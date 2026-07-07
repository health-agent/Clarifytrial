"""Patient Profile Understanding Agent.

Responsibility: build and maintain the normalized ``PatientProfile`` from
patient free text, and normalize free-text clarification answers into
structured variables BEFORE any rule update is applied (locked invariant:
raw free text never flows directly into rule evaluation).
"""

from __future__ import annotations

from typing import Any

from models import AnswerUpdate, FollowUpQuestion, PatientProfile


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
