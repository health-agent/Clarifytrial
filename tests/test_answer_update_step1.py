"""Tests for Step 1: answer normalization and profile update only."""

import os

import pytest

from agents.answer_update_reevaluation import (
    AnswerUpdateResult,
    apply_answer_update_to_patient_profile,
    normalize_clarification_answer,
)
from constants.variable_keys import CANONICAL_VARIABLE_KEYS, canonicalize_variable_key
from models import PatientProfile

API_KEY_ENV_VARS = [
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "GOOGLE_API_KEY",
]


@pytest.fixture(autouse=True)
def no_api_keys(monkeypatch):
    for var in API_KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _base_profile(**variables) -> PatientProfile:
    return PatientProfile(
        patient_id="P001",
        age=55,
        sex="female",
        conditions=["type 2 diabetes"],
        medications=["metformin"],
        variables=dict(variables),
        free_text_notes="Synthetic patient note.",
    )


@pytest.mark.parametrize("key", sorted(CANONICAL_VARIABLE_KEYS))
def test_canonicalize_variable_key_accepts_all_eleven_keys(key):
    assert canonicalize_variable_key(key) == key


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        ("egfr_mutation_status", "biomarker_status"),
        ("performance_status", "ecog_performance_status"),
        ("creatinine", "renal_function"),
    ],
)
def test_canonicalize_variable_key_aliases(alias, canonical):
    assert canonicalize_variable_key(alias) == canonical


def test_canonicalize_variable_key_unsupported_returns_none():
    assert canonicalize_variable_key("blood_pressure") is None
    assert canonicalize_variable_key("") is None


def test_clear_answer_updates_only_requested_variable_in_variables():
    profile = _base_profile(ecog_performance_status="unknown")
    result = apply_answer_update_to_patient_profile(
        profile, "Q-001", "ecog_performance_status", "ECOG 1"
    )
    assert isinstance(result, AnswerUpdateResult)
    assert result.was_actually_updated is True
    assert result.updated_variable_key == "ecog_performance_status"
    assert result.updated_patient_profile.variables["ecog_performance_status"] == 1
    assert result.updated_patient_profile.age == 55
    assert result.updated_patient_profile.sex == "female"
    assert result.updated_patient_profile.conditions == ["type 2 diabetes"]
    assert result.updated_patient_profile.medications == ["metformin"]
    assert result.updated_patient_profile.free_text_notes == "Synthetic patient note."


def test_unrelated_variables_remain_unchanged():
    profile = _base_profile(
        ecog_performance_status="unknown",
        prior_treatment="chemotherapy",
        biomarker_status="negative",
    )
    result = apply_answer_update_to_patient_profile(
        profile, "Q-002", "ecog_performance_status", "2"
    )
    assert result.was_actually_updated is True
    assert result.updated_patient_profile.variables["prior_treatment"] == "chemotherapy"
    assert result.updated_patient_profile.variables["biomarker_status"] == "negative"


def test_identical_existing_value_is_idempotent_no_op():
    profile = _base_profile(ecog_performance_status=1)
    result = apply_answer_update_to_patient_profile(
        profile, "Q-003", "ecog_performance_status", "1"
    )
    assert result.was_actually_updated is False
    assert result.unresolved_reason is None
    assert result.conflict_detected is False
    assert result.updated_patient_profile is profile
    assert result.updated_patient_profile.variables["ecog_performance_status"] == 1


def test_unclear_answer_does_not_mutate_patient_profile():
    profile = _base_profile(ecog_performance_status="unknown")
    for unclear in ("", "I don't know", "not sure", "unknown"):
        result = apply_answer_update_to_patient_profile(
            profile, "Q-004", "ecog_performance_status", unclear
        )
        assert result.was_actually_updated is False
        assert result.unresolved_reason == "unclear_answer"
        assert result.updated_patient_profile is profile
        assert profile.variables.get("ecog_performance_status") == "unknown"


def test_conflicting_value_does_not_overwrite_existing_value():
    profile = _base_profile(ecog_performance_status=1)
    result = apply_answer_update_to_patient_profile(
        profile, "Q-005", "ecog_performance_status", "3"
    )
    assert result.was_actually_updated is False
    assert result.unresolved_reason == "conflict"
    assert result.conflict_detected is True
    assert result.updated_patient_profile is profile
    assert profile.variables["ecog_performance_status"] == 1


def test_normalize_clarification_answer_alias_key():
    value, reason = normalize_clarification_answer("performance_status", "ECOG 0")
    assert value == 0
    assert reason is None


def test_unsupported_key_handled_deterministically():
    result = apply_answer_update_to_patient_profile(
        _base_profile(), "Q-006", "blood_pressure", "120/80"
    )
    assert result.was_actually_updated is False
    assert result.unresolved_reason == "unsupported_key"
    assert result.updated_variable_key is None


def test_no_external_api_key_required():
    for var in API_KEY_ENV_VARS:
        assert os.environ.get(var) is None
    profile = _base_profile()
    result = apply_answer_update_to_patient_profile(
        profile, "Q-007", "renal_function", "creatinine 1.0 mg/dL"
    )
    assert result.was_actually_updated is True
    assert "creatinine" in result.updated_patient_profile.variables["renal_function"]
