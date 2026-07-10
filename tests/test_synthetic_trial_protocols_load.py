"""Validate the mock trial protocol dataset (source-agnostic, static)."""

import json
from pathlib import Path

from models import SyntheticTrialProtocolDataset

DATASET_PATH = (
    Path(__file__).resolve().parent.parent
    / "examples"
    / "synthetic_trial_protocols.json"
)


def load_dataset() -> SyntheticTrialProtocolDataset:
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    return SyntheticTrialProtocolDataset.model_validate(data)


def test_dataset_file_exists():
    assert DATASET_PATH.is_file()


def test_dataset_validates_with_at_least_two_trials():
    dataset = load_dataset()
    assert len(dataset.trials) >= 2


def test_each_trial_has_inclusion_and_exclusion_criteria():
    dataset = load_dataset()
    for trial in dataset.trials:
        assert trial.inclusion_criteria_text and trial.inclusion_criteria_text.strip()
        assert trial.exclusion_criteria_text and trial.exclusion_criteria_text.strip()
        assert trial.eligibility_criteria_raw.strip()


def test_each_trial_has_static_source_fields_no_live_api():
    dataset = load_dataset()
    for trial in dataset.trials:
        assert trial.source == "synthetic_mock"
        assert trial.source_url is not None
        # retrieved_at is present as STATIC data — nothing is fetched live,
        # and no ClinicalTrials.gov API response paths are assumed.
        assert trial.retrieved_at is not None
