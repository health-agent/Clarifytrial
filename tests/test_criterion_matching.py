"""Tests for the deterministic criterion matching fallback."""

import pytest

from agents.criterion_matching import match_criterion_against_patient
from models import (
    Criterion,
    CriterionMatchStatus,
    CriterionState,
    CriterionType,
    EligibilityEffect,
    PatientProfile,
)
from rules import derive_eligibility_effect

API_KEY_ENV_VARS = [
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "GOOGLE_API_KEY",
]


@pytest.fixture(autouse=True)
def no_api_keys(monkeypatch):
    """Matching must work with no API keys in the environment."""
    for var in API_KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def make_profile(**variables) -> PatientProfile:
    age = variables.pop("age", None)
    return PatientProfile(patient_id="p-test", age=age, variables=variables)


def make_criterion(
    cid: str, ctype: CriterionType, text: str, required: list[str]
) -> Criterion:
    return Criterion(
        criterion_id=cid,
        trial_id="T-1",
        criterion_type=ctype,
        text=text,
        required_variables=required,
    )


def test_returns_valid_criterion_state():
    state = match_criterion_against_patient(
        make_profile(),
        make_criterion("T-1-INC-01", CriterionType.inclusion,
                       "ECOG performance status 0-1", ["ecog_performance_status"]),
    )
    assert isinstance(state, CriterionState)
    assert state.criterion_id == "T-1-INC-01"
    assert state.trial_id == "T-1"
    assert state.criterion_type == CriterionType.inclusion


def test_matching_diagnosis_becomes_met():
    state = match_criterion_against_patient(
        make_profile(diagnosis="mock carcinoma"),
        make_criterion("T-1-INC-01", CriterionType.inclusion,
                       "Histologically confirmed mock carcinoma", ["diagnosis"]),
    )
    assert state.criterion_match_status == CriterionMatchStatus.met
    assert state.eligibility_effect == EligibilityEffect.supports_eligibility


def test_age_threshold_met_and_unmet():
    criterion = make_criterion("T-1-INC-02", CriterionType.inclusion,
                               "Age 18 years or older", ["age"])
    met = match_criterion_against_patient(make_profile(age=62), criterion)
    assert met.criterion_match_status == CriterionMatchStatus.met

    unmet = match_criterion_against_patient(make_profile(age=16), criterion)
    assert unmet.criterion_match_status == CriterionMatchStatus.unmet
    assert unmet.eligibility_effect == EligibilityEffect.blocks_eligibility


def test_missing_required_variable_becomes_unknown_with_missing_key():
    state = match_criterion_against_patient(
        make_profile(),
        make_criterion("T-1-INC-03", CriterionType.inclusion,
                       "ECOG performance status 0-1", ["ecog_performance_status"]),
    )
    assert state.criterion_match_status == CriterionMatchStatus.unknown
    assert state.missing_variable_keys == ["ecog_performance_status"]
    assert state.eligibility_effect == EligibilityEffect.uncertain


def test_missing_information_is_never_negative_evidence():
    # Exclusion criterion with unknown prior treatment: must be unknown
    # (uncertain), NOT unmet (which would count as supporting evidence).
    state = match_criterion_against_patient(
        make_profile(),
        make_criterion("T-1-EXC-01", CriterionType.exclusion,
                       "Prior systemic therapy", ["prior_treatment"]),
    )
    assert state.criterion_match_status == CriterionMatchStatus.unknown
    assert "prior_treatment" in state.missing_variable_keys


def test_exclusion_semantics_preserved_through_effect_derivation():
    # Patient explicitly HAS the excluded prior treatment -> met -> blocks.
    met_state = match_criterion_against_patient(
        make_profile(prior_treatment=True),
        make_criterion("T-1-EXC-01", CriterionType.exclusion,
                       "Prior systemic therapy", ["prior_treatment"]),
    )
    assert met_state.criterion_match_status == CriterionMatchStatus.met
    assert met_state.eligibility_effect == EligibilityEffect.blocks_eligibility

    # Patient explicitly does NOT have it -> unmet -> supports.
    unmet_state = match_criterion_against_patient(
        make_profile(prior_treatment=False),
        make_criterion("T-1-EXC-02", CriterionType.exclusion,
                       "Prior systemic therapy", ["prior_treatment"]),
    )
    assert unmet_state.criterion_match_status == CriterionMatchStatus.unmet
    assert unmet_state.eligibility_effect == EligibilityEffect.supports_eligibility


def test_conflict_value_routes_to_review_via_rules():
    state = match_criterion_against_patient(
        make_profile(comorbidities="conflict"),
        make_criterion("T-1-EXC-03", CriterionType.exclusion,
                       "Uncontrolled cardiac disease", ["comorbidities"]),
    )
    assert state.criterion_match_status == CriterionMatchStatus.conflict
    assert state.review_required is True


@pytest.mark.parametrize("ctype", [CriterionType.inclusion, CriterionType.exclusion])
def test_effect_always_equals_rule_output(ctype):
    profiles = [
        make_profile(),
        make_profile(age=62),
        make_profile(prior_treatment=True),
        make_profile(comorbidities="conflict"),
    ]
    criterion = make_criterion(
        "T-1-X-01", ctype, "Age 18 years or older prior therapy cardiac",
        ["age", "prior_treatment", "comorbidities"],
    )
    for profile in profiles:
        state = match_criterion_against_patient(profile, criterion)
        expected = derive_eligibility_effect(ctype, state.criterion_match_status)
        assert (state.eligibility_effect, state.review_required, state.review_reason) == expected


def test_demo_script_runs_and_writes_report():
    from scripts.run_criterion_matching_demo import REPORT_PATH, main

    states = main()
    assert len(states) >= 5
    assert any(s.criterion_match_status == CriterionMatchStatus.unknown for s in states)
    assert REPORT_PATH.is_file()
    assert "SYNTHETIC" in REPORT_PATH.read_text(encoding="utf-8")
