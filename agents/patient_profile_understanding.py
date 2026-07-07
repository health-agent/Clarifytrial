"""Patient Profile Understanding Agent.

Responsibility: build and maintain the normalized ``PatientProfile`` from
patient free text, and normalize free-text clarification answers into
structured variables BEFORE any rule update is applied (locked invariant:
raw free text never flows directly into rule evaluation).

Current extraction implementation: a deterministic, offline heuristic
fallback (regex/keyword based). It makes no network calls and requires no
API keys. LLM structured extraction, driven by
``prompts/patient_profile_extraction.md``, will replace the fallback
behind the same typed contract.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from models import AnswerUpdate, FollowUpQuestion, PatientProfile

# Small keyword list for obvious diagnoses in the synthetic datasets.
# Deliberately minimal: this is a heuristic placeholder, not an NLP engine.
_DIAGNOSIS_KEYWORDS = [
    "non-small cell lung cancer",
    "lung cancer",
    "breast cancer",
    "prostate cancer",
    "type 2 diabetes",
    "type 1 diabetes",
    "multiple sclerosis",
    "ulcerative colitis",
    "heart failure",
    "major depressive disorder",
    "pulmonary fibrosis",
    "hepatitis b",
    "pancreatitis",
]

_AGE_YEARS_RE = re.compile(r"\b(\d{1,3})[- ]year[- ]old\b", re.IGNORECASE)
_AGE_MONTHS_RE = re.compile(r"\b(\d{1,2})[- ]month[- ]old\b", re.IGNORECASE)
_FEMALE_RE = re.compile(r"\b(woman|female|girl)\b", re.IGNORECASE)
_MALE_RE = re.compile(r"\b(man|male|boy)\b", re.IGNORECASE)
_STAGE_RE = re.compile(r"\bstage\s+([0IVX]+[AB]?)\b", re.IGNORECASE)


def _extract_age(text: str) -> Optional[int]:
    match = _AGE_YEARS_RE.search(text)
    if match:
        return int(match.group(1))
    if _AGE_MONTHS_RE.search(text):
        return 0  # under 1 year, stated in months
    return None


def _extract_sex(text: str) -> Optional[str]:
    # Check female first: "woman" contains "man" without word boundaries.
    if _FEMALE_RE.search(text):
        return "female"
    if _MALE_RE.search(text):
        return "male"
    return None


def extract_patient_profile_from_summary(
    patient_id: str, summary_text: str
) -> PatientProfile:
    """Extract a structured profile from a natural-language case summary.

    Input contract: the summary is a patient INPUT only, never eligibility
    ground truth. Always returns a valid ``PatientProfile``; anything the
    heuristics cannot find is preserved as None / "unknown".

    Offline deterministic fallback — no LLM, no network, no API keys.

    TODO: replace the heuristics below with LLM structured extraction
    using prompts/patient_profile_extraction.md (schema-constrained JSON
    validated against PatientProfile), keeping this exact signature.
    """
    age = _extract_age(summary_text)
    sex = _extract_sex(summary_text)

    lowered = summary_text.lower()
    conditions = [kw for kw in _DIAGNOSIS_KEYWORDS if kw in lowered]
    # Drop generic terms shadowed by a more specific match
    # (e.g. keep "non-small cell lung cancer" over "lung cancer").
    conditions = [
        c for c in conditions if not any(c != o and c in o for o in conditions)
    ]

    stage_match = _STAGE_RE.search(summary_text)

    variables: dict[str, Any] = {
        "diagnosis": conditions[0] if conditions else "unknown",
        "stage": stage_match.group(1).upper() if stage_match else "unknown",
        # TODO: LLM extraction will populate these from the summary;
        # the heuristic fallback leaves them explicitly unknown.
        "biomarkers": "unknown",
        "prior_treatments": "unknown",
        "current_treatments": "unknown",
        "comorbidities": "unknown",
    }

    return PatientProfile(
        patient_id=patient_id,
        age=age,
        sex=sex,
        conditions=conditions,
        medications=[],
        variables=variables,
        free_text_notes=summary_text,
    )


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
