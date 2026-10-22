"""Regression cases found in live public protocols, not policy score tests."""

import pytest

from clarifytrial.datasets.source_criteria import _numeric_rows
from clarifytrial.preparation.team_trials import TeamTrialRecord


def extract(line):
    record = TeamTrialRecord(nct_id="NCT00000001", title="Synthetic protocol",
                             conditions=["Synthetic"], overall_status="RECRUITING",
                             eligibility_text="Inclusion Criteria:\n" + line)
    return _numeric_rows(record, "synthetic")


@pytest.mark.parametrize("line,unit,value", [
    ("* Serum creatinine <= 1.6 mg/dl.", "mg/dL", 1.6),
    ("* Hemoglobin >= 9.0 g/dL", "g/dL", 9.0),
    ("* Hemoglobin >= 90 g/L", "g/L", 90.0),
    ("* Absolute neutrophil count >= 1.5 x 10\\^3/uL", "/uL", 1500),
    ("* Platelets >= 100 x 10^3/uL", "/uL", 100000),
    ("* Platelets >= 100 x 10^9/L", "10^9/L", 100),
    ("* eGFR <30 ml/min", "mL/min", 30),
])
def test_units_and_multipliers_are_not_silently_changed(line, unit, value):
    rows = extract(line)
    assert len(rows) == 1
    assert rows[0]["unit"] == unit
    assert rows[0]["threshold"] == value


@pytest.mark.parametrize("line", [
    "* Self-reported change in body weight >5 kg within 3 months.",
    "* Have a stable body weight (<5% body weight change) for 90 days.",
    "* Interdialytic weight gain of >2 liters.",
    "* Dry weight >=7 kg.",
    "* History of >=1 unsuccessful diet effort to lose body weight.",
    "* Body mass index (BMI >25-40 kg/m2)",
    "* AST <= 3 x ULN (5 x ULN allowed if liver metastases)",
    "* Treatment excluding EGFR TKIs <=14 days prior to study drug.",
    "* eGFR <40",  # Unstated unit remains unstructured.
])
def test_unsupported_meaning_is_left_unstructured(line):
    assert not extract(line)
