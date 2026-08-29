from __future__ import annotations

from datetime import date

import pytest

from clarifytrial.measurements import units_equivalent
from clarifytrial.preparation.contracts import PatientFactDraft, TrialCriterionDraft
from clarifytrial.preparation.source_validation import (
    SourceValidationError,
    remove_unsupported_evidence_requirements,
    remove_unwritten_equality_constraint,
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


def test_markdown_escaped_comparison_symbol_matches_plain_quote() -> None:
    source = r"Severe dysfunction means serum creatinine \>177 μmol/L."

    match = resolve_source_span(source, "serum creatinine >177 μmol/L")

    assert match.source_text == r"serum creatinine \>177 μmol/L"


def test_repeated_short_quote_requires_a_location_hint() -> None:
    source = "HbA1c reviewed. HbA1c was 6.5 %."

    with pytest.raises(SourceValidationError, match="appears more than once"):
        resolve_source_span(source, "HbA1c")


def test_repeated_quote_uses_nearby_meaning_when_context_is_unique() -> None:
    source = (
        "Steroid treatment was continued for at least 3 months. "
        "Unrelated text. Antibody treatment was continued for at least 3 months."
    )

    match = resolve_source_span(
        source,
        "for at least 3 months",
        context_hint="antibody treatment duration",
    )

    assert match.start_char == source.rindex("for at least 3 months")
    assert match.match_method == "normalized_context_match"


def test_repeated_quote_can_be_assigned_by_declared_occurrence() -> None:
    source = "First rule. Shared sentence. Second rule. Shared sentence."

    first = resolve_source_span(
        source,
        "Shared sentence.",
        occurrence_index=0,
    )
    second = resolve_source_span(
        source,
        "Shared sentence.",
        occurrence_index=1,
    )

    assert first.start_char == source.index("Shared sentence.")
    assert second.start_char == source.rindex("Shared sentence.")
    assert second.match_method == "normalized_occurrence_match"

    reused = resolve_source_span(
        source,
        "Shared sentence.",
        occurrence_index=2,
    )
    assert reused.start_char == second.start_char
    assert reused.match_method == "normalized_reused_occurrence_match"


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


def test_patient_numeric_concept_must_match_the_cited_source() -> None:
    fact = PatientFactDraft(
        fact_key="hba1c",
        statement="HbA1c was 7.0%.",
        source_quote="Platelet count was 7.0 % on 2026-05-01.",
        event_date=date(2026, 5, 1),
        concept="hba1c",
        value=7.0,
        unit="percent",
    )

    with pytest.raises(SourceValidationError, match="patient concept"):
        validate_patient_fact_source(fact, fact.source_quote)


@pytest.mark.parametrize(
    ("text", "value", "unit"),
    [
        ("A 54-year-old man presents with severe pain.", 54, "years"),
        ("A 3-month-old infant has repeated vomiting.", 3, "months"),
    ],
)
def test_hyphenated_age_is_supported_by_its_number_and_unit(
    text: str,
    value: float,
    unit: str,
) -> None:
    fact = PatientFactDraft(
        fact_key="age",
        statement=text,
        source_quote=text,
        concept="age",
        value=value,
        unit=unit,
    )

    validate_patient_fact_source(fact, text)


def test_patient_event_date_cannot_be_added_when_source_has_no_date() -> None:
    fact = PatientFactDraft(
        fact_key="hba1c",
        statement="HbA1c was 7.0%.",
        source_quote="HbA1c was 7.0 %.",
        event_date=date(2026, 5, 1),
        concept="hba1c",
        value=7.0,
        unit="percent",
    )

    with pytest.raises(SourceValidationError, match="event date is not present"):
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


def test_unwritten_evidence_rules_are_removed_but_supported_pathology_is_kept() -> None:
    text = "Histopathologically confirmed breast cancer"
    criterion = TrialCriterionDraft(
        kind="inclusion",
        statement=text,
        source_quote=text,
        evidence_requirement={
            "allowed_source_types": [
                "medical_record",
                "official_verification",
            ],
            "allowed_verification_statuses": ["verified"],
        },
    )

    sanitized, corrections = remove_unsupported_evidence_requirements(
        criterion,
        text,
    )

    assert sanitized.evidence_requirement is not None
    assert sanitized.evidence_requirement.max_age_days is None
    assert [
        item.value for item in sanitized.evidence_requirement.allowed_source_types
    ] == ["official_verification"]
    assert [
        item.value
        for item in sanitized.evidence_requirement.allowed_verification_statuses
    ] == ["verified"]
    assert len(corrections) == 1
    validate_trial_criterion_source(sanitized, text)


def test_unresolved_complex_logic_can_drop_an_uncited_recency_rule() -> None:
    text = "MCTSI score ≥4"
    criterion = TrialCriterionDraft(
        kind="exclusion",
        statement=text,
        source_quote=text,
        numeric_constraint={
            "concept": "MCTSI score",
            "operator": "gte",
            "threshold": 4,
            "unit": "points",
        },
        evidence_requirement={"max_age_days": 2},
    )

    sanitized, corrections = remove_unsupported_evidence_requirements(
        criterion,
        text,
        allow_remove_recency=True,
    )

    assert sanitized.evidence_requirement is None
    assert len(corrections) == 1
    validate_trial_criterion_source(sanitized, text)


def test_forty_eight_hours_supports_a_two_day_requirement() -> None:
    text = "Modified Marshall score ≥2 within 48 hours"
    criterion = TrialCriterionDraft(
        kind="exclusion",
        statement=text,
        source_quote=text,
        numeric_constraint={
            "concept": "modified Marshall score",
            "operator": "gte",
            "threshold": 2,
            "unit": "points",
        },
        evidence_requirement={"max_age_days": 2},
    )

    validate_trial_criterion_source(criterion, text)


def test_unwritten_equality_at_a_timepoint_is_left_to_text_judgment() -> None:
    text = "Stable disease after initial 3 months of chemotherapy"
    criterion = TrialCriterionDraft(
        kind="inclusion",
        statement=text,
        source_quote=text,
        numeric_constraint={
            "concept": "chemotherapy_duration",
            "operator": "eq",
            "threshold": 3,
            "unit": "months",
        },
    )

    sanitized, corrections = remove_unwritten_equality_constraint(
        criterion,
        text,
    )

    assert sanitized.numeric_constraint is None
    assert len(corrections) == 1
    validate_trial_criterion_source(sanitized, text)


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
        ("ages 6 months -18 years old", "age", "gte", 6, "months"),
        ("Minimum Age: 18 Years", "age", "gte", 18, "years"),
        ("Maximum Age: 70 Years", "age", "lte", 70, "years"),
        ("being over the age of 19", "age", "gt", 19, "years"),
        ("More than 18 years of age", "age", "gt", 18, "years"),
        ("Patient aged 18 or over", "age", "gte", 18, "years"),
        (
            "No occurrence of macrophage activation syndrome within 1 month",
            "time_since_macrophage_activation_syndrome",
            "gt",
            1,
            "months",
        ),
        (
            "toxicity not yet improved to NCI-CTCAE version 5.0 Grade ≤1",
            "toxicity_grade",
            "gt",
            1,
            "NCI-CTCAE version 5.0 grade",
        ),
        (
            "type 2 non communicating block and higher blocks",
            "bismuth_corlette_block_type",
            "gte",
            2,
            "type",
        ),
        ("BMI ≥27 kg/m\\^2", "bmi", "gte", 27, "kg/m^2"),
        ("BMI of 23 kg/m2 or greater", "bmi", "gte", 23, "kg/m2"),
        (
            "Received at least one chemotherapy cycle",
            "chemotherapy_cycles_received",
            "gte",
            1,
            "cycles",
        ),
        ("ECOG performance status ≤ 2", "ecog_status", "lte", 2, "score"),
        (
            "ECOG Performance Status score of 0-2.",
            "ecog_status",
            "lte",
            2,
            "score",
        ),
        (
            "ECOG \\>2",
            "ECOG",
            "gt",
            2,
            "score",
        ),
        (
            "Major cardiovascular event within the past 12 months",
            "time_since_major_cardiovascular_event",
            "lte",
            12,
            "months",
        ),
        (
            "Fewer than 15 headache days (migraine or non-migraine) per month",
            "headache_days_per_month",
            "lt",
            15,
            "days/month",
        ),
        (
            "Creatinine ≤ 1.5 ULN",
            "creatinine",
            "lte",
            1.5,
            "x ULN",
        ),
        (
            "Liver minus gross tumor volume at least 700cc",
            "liver_minus_gross_tumor_volume",
            "gte",
            700,
            "cc",
        ),
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
