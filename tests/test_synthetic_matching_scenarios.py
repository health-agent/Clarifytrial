"""Validate the labeled synthetic matching scenario dataset.

Scenarios are separate from patient summaries: they carry expected
recommendation labels and expected missing variables purely to test the
locked rules — they are synthetic rule-validation examples, not clinical
truth.
"""

import json
from pathlib import Path

from models import Recommendation, SyntheticMatchingScenarioDataset

DATASET_PATH = (
    Path(__file__).resolve().parent.parent
    / "examples"
    / "synthetic_matching_scenarios.json"
)


def load_dataset() -> SyntheticMatchingScenarioDataset:
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    return SyntheticMatchingScenarioDataset.model_validate(data)


def test_dataset_file_exists():
    assert DATASET_PATH.is_file()


def test_dataset_validates_with_at_least_four_scenarios():
    dataset = load_dataset()
    assert len(dataset.scenarios) >= 4


def test_all_four_recommendation_labels_covered():
    dataset = load_dataset()
    labels = {s.expected_recommendation for s in dataset.scenarios}
    assert labels == set(Recommendation)


def test_every_scenario_references_patient_and_trial():
    dataset = load_dataset()
    for scenario in dataset.scenarios:
        assert scenario.patient_id.strip()
        assert scenario.trial_id.strip()
        assert scenario.patient_profile.patient_id == scenario.patient_id


def test_expected_fields_have_locked_types():
    dataset = load_dataset()
    for scenario in dataset.scenarios:
        assert isinstance(scenario.expected_missing_variables, list)
        assert isinstance(scenario.expected_blocking_criteria, list)
        assert isinstance(scenario.expected_recommendation, Recommendation)
