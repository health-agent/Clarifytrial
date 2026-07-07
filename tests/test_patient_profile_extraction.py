"""Tests for the deterministic patient profile extraction fallback."""

import json
import os
from pathlib import Path

import pytest

from agents.patient_profile_understanding import extract_patient_profile_from_summary
from models import PatientProfile, SyntheticPatientDataset

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"

API_KEY_ENV_VARS = [
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "GOOGLE_API_KEY",
]


@pytest.fixture(autouse=True)
def no_api_keys(monkeypatch):
    """Extraction must work with no API keys in the environment."""
    for var in API_KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def load_topics(filename: str):
    data = json.loads((EXAMPLES_DIR / filename).read_text(encoding="utf-8"))
    return SyntheticPatientDataset.model_validate(data).topics


def test_returns_valid_patient_profile_for_synthetic_summary():
    case = load_topics("synthetic_patients.json")[0]
    profile = extract_patient_profile_from_summary(case.num, case.title)
    assert isinstance(profile, PatientProfile)
    assert profile.patient_id == case.num
    assert profile.free_text_notes == case.title


def test_extracts_obvious_fields_from_first_synthetic_summary():
    # S001: "A 62-year-old woman with stage IIIB non-small cell lung cancer..."
    case = load_topics("synthetic_patients.json")[0]
    profile = extract_patient_profile_from_summary(case.num, case.title)
    assert profile.age == 62
    assert profile.sex == "female"
    assert profile.variables["stage"] == "IIIB"
    assert "non-small cell lung cancer" in profile.conditions


def test_vague_summary_yields_valid_profile_with_unknowns():
    profile = extract_patient_profile_from_summary(
        "vague-1", "Patient reports feeling generally unwell recently."
    )
    assert isinstance(profile, PatientProfile)
    assert profile.age is None
    assert profile.sex is None
    assert profile.conditions == []
    assert profile.variables["diagnosis"] == "unknown"
    assert profile.variables["stage"] == "unknown"


def test_all_synthetic_summaries_extract_without_error():
    for case in load_topics("synthetic_patients.json"):
        profile = extract_patient_profile_from_summary(case.num, case.title)
        PatientProfile.model_validate(profile.model_dump())


def test_professor_summaries_accepted_as_inputs_only():
    # Professor summaries are INPUTS only — never eligibility labels.
    for case in load_topics("professor_patient_summaries.json"):
        profile = extract_patient_profile_from_summary(case.num, case.title)
        assert isinstance(profile, PatientProfile)
        assert profile.free_text_notes == case.title


def test_demo_script_runs_and_writes_report():
    from scripts.run_patient_profile_extraction_demo import REPORT_PATH, main

    profiles = main()
    assert len(profiles) >= 2
    assert REPORT_PATH.is_file()
    assert "SYNTHETIC" in REPORT_PATH.read_text(encoding="utf-8")
