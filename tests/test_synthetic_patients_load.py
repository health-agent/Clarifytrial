"""Validate the professor-style synthetic patient dataset.

These case summaries are natural-language patient INPUTS only — NOT
eligibility ground truth. The tests validate the input contract of the
Patient Profile Understanding Agent, not any recommendation label.
"""

import inspect
import json
from pathlib import Path

from agents.patient_profile_understanding import extract_patient_profile_from_summary
from models import PatientProfile, SyntheticPatientDataset

DATASET_PATH = (
    Path(__file__).resolve().parent.parent / "examples" / "synthetic_patients.json"
)


def load_dataset() -> SyntheticPatientDataset:
    data = json.loads(DATASET_PATH.read_text(encoding="utf-8"))
    return SyntheticPatientDataset.model_validate(data)


def test_dataset_file_exists():
    assert DATASET_PATH.is_file()


def test_dataset_validates_with_exactly_ten_cases():
    dataset = load_dataset()
    assert len(dataset.topics) == 10


def test_every_case_has_num_and_nonempty_title():
    dataset = load_dataset()
    for case in dataset.topics:
        assert case.num.startswith("S")
        assert isinstance(case.title, str)
        assert case.title.strip()


def test_extraction_contract_matches_dataset():
    signature = inspect.signature(extract_patient_profile_from_summary)
    assert list(signature.parameters) == ["patient_id", "summary_text"]

    dataset = load_dataset()
    for case in dataset.topics:
        profile = extract_patient_profile_from_summary(case.num, case.title)
        assert isinstance(profile, PatientProfile)
        assert profile.patient_id == case.num
        # The original summary remains available and is never interpreted
        # as eligibility ground truth.
        assert profile.free_text_notes == case.title
