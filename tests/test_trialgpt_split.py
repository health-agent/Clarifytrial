from __future__ import annotations

from collections import Counter

import pytest

from clarifytrial.datasets.trialgpt import (
    TrialGPTCriterionRow,
    TrialGPTPair,
    select_full_trialgpt_pairs,
    split_trialgpt_pairs_by_patient,
)


def _criterion(
    annotation_id: int,
    patient_id: str,
    trial_id: str,
    *,
    criterion_text: str | None = "Synthetic criterion",
) -> TrialGPTCriterionRow:
    return TrialGPTCriterionRow(
        annotation_id=annotation_id,
        patient_id=patient_id,
        note="0. Synthetic patient fact.",
        trial_id=trial_id,
        trial_title=f"Synthetic trial {trial_id}",
        criterion_type="exclusion",
        criterion_text=criterion_text,
        gpt4_explanation="Synthetic public explanation",
        explanation_correctness="Correct",
        gpt4_sentences=[0],
        expert_sentences=[0],
        gpt4_eligibility="not excluded",
        expert_eligibility="not excluded",
        training=False,
    )


def _pair(
    annotation_id: int,
    patient_id: str,
    trial_id: str,
    category: str,
    *,
    criterion_text: str | None = "Synthetic criterion",
) -> TrialGPTPair:
    row = _criterion(
        annotation_id,
        patient_id,
        trial_id,
        criterion_text=criterion_text,
    )
    return TrialGPTPair(
        patient_id=patient_id,
        trial_id=trial_id,
        note=row.note,
        trial_title=row.trial_title,
        criteria=[row],
        category=category,
    )


def _two_trials_per_patient() -> list[TrialGPTPair]:
    population = {
        "clear": 10,
        "unresolved_only": 69,
        "violation_and_unresolved": 22,
        "violation_only": 4,
    }
    pairs: list[TrialGPTPair] = []
    annotation_id = 0
    for category, patient_count in population.items():
        for index in range(patient_count):
            patient_id = f"{category}-patient-{index:02d}"
            for trial_index in range(2):
                trial_id = f"{category}-trial-{index:02d}-{trial_index}"
                pairs.append(
                    _pair(
                        annotation_id,
                        patient_id,
                        trial_id,
                        category,
                    )
                )
                annotation_id += 1
    return pairs


def _pair_ids(pairs: tuple[TrialGPTPair, ...] | list[TrialGPTPair]) -> set[tuple[str, str]]:
    return {(pair.patient_id, pair.trial_id) for pair in pairs}


def test_full_pair_selection_is_complete_and_stably_sorted() -> None:
    complete_b = _pair(1, "patient-b", "trial-2", "clear")
    complete_a = _pair(0, "patient-a", "trial-1", "clear")
    incomplete = _pair(
        2,
        "patient-c",
        "trial-3",
        "unresolved_only",
        criterion_text=None,
    )

    selected = select_full_trialgpt_pairs([complete_b, incomplete, complete_a])

    assert [(pair.patient_id, pair.trial_id) for pair in selected] == [
        ("patient-a", "trial-1"),
        ("patient-b", "trial-2"),
    ]


def test_patient_split_is_deterministic_and_has_no_held_out_leakage() -> None:
    pairs = _two_trials_per_patient()

    first = split_trialgpt_pairs_by_patient(pairs)
    second = split_trialgpt_pairs_by_patient(list(reversed(pairs)))

    assert first == second
    assert len(first.development_pairs) == 20
    assert len(first.overlap_patient_pairs) == 20
    assert len(first.held_out_pairs) == 170
    assert Counter(pair.category for pair in first.development_pairs) == {
        "clear": 2,
        "unresolved_only": 13,
        "violation_and_unresolved": 4,
        "violation_only": 1,
    }

    development_patients = {
        pair.patient_id for pair in first.development_pairs
    }
    held_out_patients = {pair.patient_id for pair in first.held_out_pairs}
    overlap_patients = {
        pair.patient_id for pair in first.overlap_patient_pairs
    }
    assert development_patients.isdisjoint(held_out_patients)
    assert overlap_patients == development_patients

    partition_ids = (
        _pair_ids(first.development_pairs)
        | _pair_ids(first.held_out_pairs)
        | _pair_ids(first.overlap_patient_pairs)
    )
    assert partition_ids == _pair_ids(pairs)


def test_duplicate_patient_trial_identifier_is_rejected() -> None:
    pair = _pair(0, "patient-a", "trial-a", "clear")
    duplicate = pair.model_copy(deep=True)

    with pytest.raises(ValueError, match="identifiers must be unique"):
        select_full_trialgpt_pairs([pair, duplicate])

