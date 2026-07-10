"""Tests for the deterministic criteria parser fallback."""

import pytest

from agents.criteria_parser import parse_criteria, parse_trial_criteria_from_text
from models import Criterion, CriterionType, TrialContext, TrialProtocol

API_KEY_ENV_VARS = [
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "GOOGLE_API_KEY",
]

SAMPLE_TEXT = """A synthetic study of mock-drug-X in a mock condition.

Inclusion Criteria:
- Age 18 years or older
- ECOG performance status 0-1
- Histologically confirmed mock carcinoma, stage II-III

Exclusion Criteria:
- Prior systemic therapy for advanced disease
- Pregnancy or breastfeeding
"""


@pytest.fixture(autouse=True)
def no_api_keys(monkeypatch):
    """Parsing must work with no API keys in the environment."""
    for var in API_KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def test_returns_valid_pydantic_objects():
    context, criteria = parse_trial_criteria_from_text("T-100", SAMPLE_TEXT)
    assert isinstance(context, TrialContext)
    assert all(isinstance(c, Criterion) for c in criteria)
    assert len(criteria) == 5


def test_inclusion_and_exclusion_distinguished():
    _, criteria = parse_trial_criteria_from_text("T-100", SAMPLE_TEXT)
    by_type = {
        CriterionType.inclusion: [c for c in criteria if c.criterion_type == CriterionType.inclusion],
        CriterionType.exclusion: [c for c in criteria if c.criterion_type == CriterionType.exclusion],
    }
    assert len(by_type[CriterionType.inclusion]) == 3
    assert len(by_type[CriterionType.exclusion]) == 2
    assert any("Prior systemic therapy" in c.text for c in by_type[CriterionType.exclusion])


def test_stable_criterion_ids():
    _, criteria = parse_trial_criteria_from_text("T-100", SAMPLE_TEXT)
    ids = [c.criterion_id for c in criteria]
    assert ids == ["T-100-INC-01", "T-100-INC-02", "T-100-INC-03",
                   "T-100-EXC-01", "T-100-EXC-02"]
    # Deterministic: same input, same ids.
    _, again = parse_trial_criteria_from_text("T-100", SAMPLE_TEXT)
    assert [c.criterion_id for c in again] == ids


def test_required_variables_extracted_from_simple_criteria():
    _, criteria = parse_trial_criteria_from_text("T-100", SAMPLE_TEXT)
    by_id = {c.criterion_id: c for c in criteria}
    assert "age" in by_id["T-100-INC-01"].required_variables
    assert "ecog_performance_status" in by_id["T-100-INC-02"].required_variables
    assert "disease_stage" in by_id["T-100-INC-03"].required_variables
    assert "prior_treatment" in by_id["T-100-EXC-01"].required_variables
    assert "pregnancy_status" in by_id["T-100-EXC-02"].required_variables


def test_description_never_becomes_a_criterion():
    context, criteria = parse_trial_criteria_from_text("T-100", SAMPLE_TEXT)
    assert "mock-drug-X" in context.description
    assert all("mock-drug-X" not in c.text for c in criteria)


def test_missing_exclusion_section_parses_gracefully():
    text = "Some description.\n\nInclusion Criteria:\n- Age 18 years or older\n"
    _, criteria = parse_trial_criteria_from_text("T-200", text)
    assert len(criteria) == 1
    assert criteria[0].criterion_type == CriterionType.inclusion
    assert criteria[0].criterion_id == "T-200-INC-01"


def test_empty_text_parses_to_no_criteria():
    context, criteria = parse_trial_criteria_from_text("T-300", "")
    assert criteria == []
    assert isinstance(context, TrialContext)


def test_parse_criteria_wrapper_uses_protocol_raw_text():
    protocol = TrialProtocol(
        trial_id="T-400",
        eligibility_criteria_raw=(
            "Inclusion Criteria:\n- Age 18 years or older\n\n"
            "Exclusion Criteria:\n- Current insulin use"
        ),
    )
    criteria = parse_criteria(protocol)
    assert [c.criterion_id for c in criteria] == ["T-400-INC-01", "T-400-EXC-01"]
    assert criteria[1].criterion_type == CriterionType.exclusion


def test_demo_script_runs_and_writes_report():
    from scripts.run_criteria_parser_demo import REPORT_PATH, main

    parsed = main()
    assert len(parsed) >= 2
    assert all(len(criteria) >= 2 for criteria in parsed.values())
    assert REPORT_PATH.is_file()
    assert "SYNTHETIC" in REPORT_PATH.read_text(encoding="utf-8")
