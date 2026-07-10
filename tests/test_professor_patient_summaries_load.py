"""Validate the professor-provided patient summary dataset (read-only).

This is a SEPARATE input robustness dataset. The summaries are
natural-language patient INPUT examples only — they are NOT eligibility
ground truth or recommendation ground truth. These tests validate the
schema shape and the input contract of the Patient Profile Understanding
Agent; the dataset file itself is never modified.
"""

import json
from pathlib import Path

from agents.patient_profile_understanding import extract_patient_profile_from_summary
from models import PatientProfile, SyntheticPatientDataset

DATASET_PATH = (
    Path(__file__).resolve().parent.parent
    / "examples"
    / "professor_patient_summaries.json"
)


def load_dataset() -> SyntheticPatientDataset:
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    return SyntheticPatientDataset.model_validate(data)


def test_dataset_file_exists():
    assert DATASET_PATH.is_file()


def test_dataset_validates_as_synthetic_patient_dataset():
    dataset = load_dataset()
    assert isinstance(dataset, SyntheticPatientDataset)


def test_dataset_contains_exactly_ten_cases():
    dataset = load_dataset()
    assert len(dataset.topics) == 10


def test_every_case_has_num_and_title():
    dataset = load_dataset()
    for case in dataset.topics:
        assert case.num
        assert case.title


def test_every_num_starts_with_s():
    dataset = load_dataset()
    for case in dataset.topics:
        assert case.num.startswith("S")


def test_every_title_is_nonempty_string():
    dataset = load_dataset()
    for case in dataset.topics:
        assert isinstance(case.title, str)
        assert case.title.strip()


def test_every_title_passes_extraction_input_contract():
    dataset = load_dataset()
    for case in dataset.topics:
        profile = extract_patient_profile_from_summary(case.num, case.title)
        assert isinstance(profile, PatientProfile)
        assert profile.patient_id == case.num
        # Input-contract check only: the summary is carried as raw input,
        # never interpreted as eligibility or recommendation ground truth.
        assert profile.free_text_notes == case.title
