from __future__ import annotations

from datetime import date

import pytest

from clarifytrial.measurements import units_equivalent
from clarifytrial.preparation.contracts import PatientFactDraft, TrialCriterionDraft
from clarifytrial.preparation.source_validation import (
    SourceValidationError,
    resolve_source_span,
    validate_patient_fact_source,
    validate_trial_criterion_source,
)


@pytest.mark.parametrize(
    "quote",
    [
        "HbA1c was 6.5 %.",
        "HbA1cwas6.5%.",
        "hba1c\nwas\t6.5 %.",
        "ＨｂＡ１ｃ was 6.5％.",
        "HbA1c\u200b was 6.5 %.",
    ],
)
def test_source_location_ignores_layout_only_differences(quote: str) -> None:
    source = "Previous note. HbA1c was 6.5 %. Follow-up planned."

    match = resolve_source_span(source, quote)

    assert match.source_text == "HbA1c was 6.5 %."
    assert match.start_char == 15
    assert match.end_char == 15 + len("HbA1c was 6.5 %.")


def test_wrong_offset_hint_is_ignored_when_quote_has_one_match() -> None:
    source = "Previous note. HbA1c was 6.5 %. Follow-up planned."

    match = resolve_source_span(
        source,
        "HbA1c was 6.5 %.",
        approximate_start_char=0,
        approximate_end_char=5,
    )

    assert match.start_char == 15
    assert match.match_method == "normalized_unique_match"


def test_repeated_short_quote_requires_a_location_hint() -> None:
    source = "HbA1c reviewed. HbA1c was 6.5 %."

    with pytest.raises(SourceValidationError, match="appears more than once"):
        resolve_source_span(source, "HbA1c")


def test_patient_numeric_value_and_explicit_date_must_match_source() -> None:
    fact = PatientFactDraft(
        fact_key="hba1c",
        statement="HbA1c was 6.4%.",
        source_quote="HbA1c was 6.5 % on 2026-05-01.",
        event_date=date(2026, 5, 1),
        concept="hba1c",
        value=6.4,
        unit="percent",
    )

    with pytest.raises(SourceValidationError, match="patient value 6.4"):
        validate_patient_fact_source(fact, fact.source_quote)


def test_trial_requirement_cannot_add_an_unwritten_official_source() -> None:
    criterion = TrialCriterionDraft(
        kind="inclusion",
        statement="HbA1c must be below 7%.",
        source_quote="HbA1c must be below 7.0 % within 14 days.",
        numeric_constraint={
            "concept": "hba1c",
            "operator": "lt",
            "threshold": 7.0,
            "unit": "%",
        },
        evidence_requirement={
            "max_age_days": 14,
            "allowed_source_types": ["official_verification"],
        },
    )

    with pytest.raises(SourceValidationError, match="official_verification"):
        validate_trial_criterion_source(criterion, criterion.source_quote)


def test_two_weeks_supports_a_fourteen_day_requirement() -> None:
    text = "An official lab result within two weeks must show HbA1c below 7 %."
    criterion = TrialCriterionDraft(
        kind="inclusion",
        statement="HbA1c must be below 7%.",
        source_quote=text,
        numeric_constraint={
            "concept": "hba1c",
            "operator": "lt",
            "threshold": 7.0,
            "unit": "%",
        },
        evidence_requirement={
            "max_age_days": 14,
            "allowed_source_types": ["official_verification"],
            "allowed_verification_statuses": ["verified"],
        },
    )

    validate_trial_criterion_source(criterion, text)


@pytest.mark.parametrize(
    ("left", "right"),
    [
        ("%", "percent"),
        ("10^9/L", "10⁹ / litre"),
        ("µg/mL", "ug per ml"),
    ],
)
def test_equivalent_unit_notation_is_accepted(left: str, right: str) -> None:
    assert units_equivalent(left, right)


def test_unit_conversion_is_not_assumed() -> None:
    assert not units_equivalent("mg/dL", "mmol/L")


@pytest.mark.parametrize(
    ("text", "concept", "operator", "threshold", "unit"),
    [
        ("Age 45 - 70 years.", "age", "gte", 45, "years"),
        ("Age 45 - 70 years.", "age", "lte", 70, "years"),
        ("being over the age of 19", "age", "gt", 19, "years"),
        ("BMI ≥27 kg/m\\^2", "bmi", "gte", 27, "kg/m^2"),
        ("BMI of 23 kg/m2 or greater", "bmi", "gte", 23, "kg/m2"),
    ],
)
def test_common_protocol_range_and_unit_notation_is_supported(
    text: str,
    concept: str,
    operator: str,
    threshold: float,
    unit: str,
) -> None:
    criterion = TrialCriterionDraft(
        kind="inclusion",
        statement=text,
        source_quote=text,
        numeric_constraint={
            "concept": concept,
            "operator": operator,
            "threshold": threshold,
            "unit": unit,
        },
    )

    validate_trial_criterion_source(criterion, text)


@pytest.mark.parametrize(
    ("operator", "threshold"),
    [("gte", 70), ("lte", 45)],
)
def test_numeric_range_does_not_accept_reversed_bounds(
    operator: str,
    threshold: float,
) -> None:
    text = "Age 45 - 70 years."
    criterion = TrialCriterionDraft(
        kind="inclusion",
        statement=text,
        source_quote=text,
        numeric_constraint={
            "concept": "age",
            "operator": operator,
            "threshold": threshold,
            "unit": "years",
        },
    )

    with pytest.raises(SourceValidationError):
        validate_trial_criterion_source(criterion, text)
