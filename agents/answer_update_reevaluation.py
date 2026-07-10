"""Answer Update & Targeted Re-evaluation Agent.

Responsibility: after a clarification answer is normalized, apply it to the
patient profile and (in later steps) re-evaluate ONLY the criteria affected
by the answered ``missing_variable_key``.

Step 1 (current): deterministic answer normalization and profile update only.
Targeted re-evaluation is not implemented yet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from constants.variable_keys import canonicalize_variable_key
from models import AnswerUpdate, CriterionState, PatientProfile, PatientSession

_UNCLEAR_ANSWER_PHRASES = frozenset(
    {
        "i don't know",
        "i dont know",
        "not sure",
        "unknown",
        "unsure",
        "n/a",
        "na",
        "no idea",
    }
)

_ECOG_RE = re.compile(r"\becog\s*([0-3])\b", re.IGNORECASE)
_DIGIT_RE = re.compile(r"\b([0-3])\b")
_AGE_RE = re.compile(r"\b(\d{1,3})\b")
_HBA1C_RE = re.compile(r"(\d+\.?\d*)\s*%?")
_FEMALE_RE = re.compile(r"\b(female|woman)\b", re.IGNORECASE)
_MALE_RE = re.compile(r"\b(male|man)\b", re.IGNORECASE)
_PREGNANT_RE = re.compile(r"\b(pregnant|yes)\b", re.IGNORECASE)
_NOT_PREGNANT_RE = re.compile(r"\b(not pregnant|no)\b", re.IGNORECASE)


@dataclass
class AnswerUpdateResult:
    """Outcome of applying a clarification answer to a patient profile."""

    updated_patient_profile: PatientProfile
    updated_variable_key: str | None
    was_actually_updated: bool
    unresolved_reason: str | None
    conflict_detected: bool
    previous_value: Any
    new_value: Any


def _is_unclear_answer(answer_text: str) -> bool:
    stripped = answer_text.strip()
    if not stripped:
        return True
    return stripped.lower().rstrip(".") in _UNCLEAR_ANSWER_PHRASES


def _has_meaningful_existing_value(value: Any) -> bool:
    return value is not None and value != "" and value != "unknown"


def _values_equal(existing: Any, new: Any) -> bool:
    return existing == new


def _values_conflict(existing: Any, new: Any) -> bool:
    if not _has_meaningful_existing_value(existing):
        return False
    return not _values_equal(existing, new)


def normalize_clarification_answer(
    missing_variable_key: str, answer_text: str
) -> tuple[Any | None, str | None]:
    """Deterministically normalize a clarification answer for one variable.

    Returns ``(normalized_value, unresolved_reason)``. On success
    ``unresolved_reason`` is ``None``. On failure ``normalized_value`` is
    ``None`` and ``unresolved_reason`` explains why (e.g. ``"unclear_answer"``,
    ``"unsupported_key"``).

    TODO: replace heuristics with LLM structured extraction using
    ``prompts/answer_update_targeted_reevaluation.md``, keeping this signature.
    """
    canonical_key = canonicalize_variable_key(missing_variable_key)
    if canonical_key is None:
        return None, "unsupported_key"

    if _is_unclear_answer(answer_text):
        return None, "unclear_answer"

    stripped = answer_text.strip()

    if canonical_key == "ecog_performance_status":
        match = _ECOG_RE.search(stripped) or _DIGIT_RE.search(stripped)
        if match:
            return int(match.group(1)), None
        return None, "unclear_answer"

    if canonical_key == "age":
        match = _AGE_RE.search(stripped)
        if match:
            return int(match.group(1)), None
        return None, "unclear_answer"

    if canonical_key == "sex":
        if _FEMALE_RE.search(stripped):
            return "female", None
        if _MALE_RE.search(stripped):
            return "male", None
        return None, "unclear_answer"

    if canonical_key == "hba1c":
        match = _HBA1C_RE.search(stripped)
        if match:
            return float(match.group(1)), None
        return None, "unclear_answer"

    if canonical_key == "pregnancy_status":
        if _NOT_PREGNANT_RE.search(stripped):
            return "not_pregnant", None
        if _PREGNANT_RE.search(stripped):
            return "pregnant", None
        return None, "unclear_answer"

    # Text-valued variables: biomarker_status, prior_treatment, current_treatment,
    # disease_stage, comorbidities, renal_function.
    return stripped, None


def apply_answer_update_to_patient_profile(
    patient_profile: PatientProfile,
    question_id: str,
    missing_variable_key: str,
    answer_text: str,
) -> AnswerUpdateResult:
    """Apply a clarification answer to ``patient_profile.variables`` only.

    Updates at most one canonical variable. Does not mutate ``age``, ``sex``,
    ``conditions``, ``medications``, or ``free_text_notes`` directly.

    Idempotent: if the existing value equals the new normalized value,
    ``was_actually_updated`` is ``False`` and ``unresolved_reason`` is ``None``.

    *question_id* is accepted for traceability; not used in Step 1 logic.
    """
    del question_id  # reserved for future RequestLog / queue integration

    canonical_key = canonicalize_variable_key(missing_variable_key)
    if canonical_key is None:
        return AnswerUpdateResult(
            updated_patient_profile=patient_profile,
            updated_variable_key=None,
            was_actually_updated=False,
            unresolved_reason="unsupported_key",
            conflict_detected=False,
            previous_value=None,
            new_value=None,
        )

    normalized_value, normalize_reason = normalize_clarification_answer(
        missing_variable_key, answer_text
    )
    if normalize_reason is not None:
        return AnswerUpdateResult(
            updated_patient_profile=patient_profile,
            updated_variable_key=canonical_key,
            was_actually_updated=False,
            unresolved_reason=normalize_reason,
            conflict_detected=False,
            previous_value=patient_profile.variables.get(canonical_key),
            new_value=None,
        )

    previous_value = patient_profile.variables.get(canonical_key)

    if _values_conflict(previous_value, normalized_value):
        return AnswerUpdateResult(
            updated_patient_profile=patient_profile,
            updated_variable_key=canonical_key,
            was_actually_updated=False,
            unresolved_reason="conflict",
            conflict_detected=True,
            previous_value=previous_value,
            new_value=normalized_value,
        )

    if _has_meaningful_existing_value(previous_value) and _values_equal(
        previous_value, normalized_value
    ):
        return AnswerUpdateResult(
            updated_patient_profile=patient_profile,
            updated_variable_key=canonical_key,
            was_actually_updated=False,
            unresolved_reason=None,
            conflict_detected=False,
            previous_value=previous_value,
            new_value=normalized_value,
        )

    new_variables = dict(patient_profile.variables)
    new_variables[canonical_key] = normalized_value
    updated_profile = patient_profile.model_copy(update={"variables": new_variables})

    return AnswerUpdateResult(
        updated_patient_profile=updated_profile,
        updated_variable_key=canonical_key,
        was_actually_updated=True,
        unresolved_reason=None,
        conflict_detected=False,
        previous_value=previous_value,
        new_value=normalized_value,
    )


def apply_answer_update(
    session: PatientSession, update: AnswerUpdate
) -> PatientSession:
    """Apply a normalized answer to the shared state.

    TODO: mark the question answered, mark the pool item resolved, write
    the normalized value into the patient profile, and bump/validate the
    session-level clarification_round_count (max 3).
    """
    raise NotImplementedError("Answer application not implemented in skeleton")


def reevaluate_affected_criteria(
    session: PatientSession, update: AnswerUpdate
) -> list[CriterionState]:
    """Re-run matching only for criteria affected by the answered variable.

    TODO: look up affected criterion ids via the global missing variable
    pool, call the Criterion Matching Agent for each, then let the
    Eligibility State Tracker re-derive effects via rules.
    """
    raise NotImplementedError("Targeted re-evaluation not implemented in skeleton")
