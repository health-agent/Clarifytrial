from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import pytest
from pydantic import ValidationError

from clarifytrial.datasets.trialgpt import (
    TrialGPTCriterionRow,
    fetch_trialgpt_dataset,
    group_patient_trial_pairs,
    load_trialgpt_rows,
    select_pilot_pairs,
    summarize_trialgpt_rows,
)


def _row(
    annotation_id: int,
    patient_id: str,
    trial_id: str,
    *,
    expert: str = "not excluded",
    criterion_type: str = "exclusion",
) -> TrialGPTCriterionRow:
    return TrialGPTCriterionRow(
        annotation_id=annotation_id,
        patient_id=patient_id,
        note="0. Synthetic patient fact.",
        trial_id=trial_id,
        trial_title=f"Synthetic trial {trial_id}",
        criterion_type=criterion_type,
        criterion_text="Synthetic criterion.",
        gpt4_explanation="Public baseline explanation.",
        explanation_correctness="Correct",
        gpt4_sentences=[0],
        expert_sentences=[0],
        gpt4_eligibility=expert,
        expert_eligibility=expert,
        training=False,
    )


def test_fetch_writes_validated_rows_and_source_metadata(tmp_path: Path) -> None:
    public_rows = [_row(0, "p1", "t1"), _row(1, "p2", "t2")]

    def fake_fetch(_: str):
        return {
            "num_rows_total": 2,
            "rows": [{"row": row.model_dump(mode="json")} for row in public_rows],
        }

    raw_path, metadata_path = fetch_trialgpt_dataset(
        tmp_path,
        fetch_json=fake_fetch,
        expected_total=2,
        page_size=2,
    )

    assert [row.annotation_id for row in load_trialgpt_rows(raw_path)] == [0, 1]
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    assert metadata["license"] == "public-domain"
    assert metadata["row_count"] == 2


def test_grouping_separates_pair_state_categories() -> None:
    rows = [
        _row(0, "p1", "t1"),
        _row(1, "p2", "t2", expert="not enough information"),
        _row(
            2,
            "p3",
            "t3",
            expert="not included",
            criterion_type="inclusion",
        ),
        _row(3, "p3", "t3", expert="not enough information"),
        _row(
            4,
            "p4",
            "t4",
            expert="excluded",
            criterion_type="exclusion",
        ),
    ]

    categories = {
        (pair.patient_id, pair.trial_id): pair.category
        for pair in group_patient_trial_pairs(rows)
    }

    assert categories == {
        ("p1", "t1"): "clear",
        ("p2", "t2"): "unresolved_only",
        ("p3", "t3"): "violation_and_unresolved",
        ("p4", "t4"): "violation_only",
    }


def test_pilot_sampler_has_pinned_strata_and_is_reproducible() -> None:
    rows: list[TrialGPTCriterionRow] = []
    annotation_id = 0
    specifications = {
        "clear": (10, "not excluded", "exclusion"),
        "unresolved_only": (69, "not enough information", "exclusion"),
        "violation_and_unresolved": (22, "not included", "inclusion"),
        "violation_only": (4, "excluded", "exclusion"),
    }
    for category, (count, expert, criterion_type) in specifications.items():
        for index in range(count):
            patient_id = f"{category}-p{index}"
            trial_id = f"{category}-t{index}"
            rows.append(
                _row(
                    annotation_id,
                    patient_id,
                    trial_id,
                    expert=expert,
                    criterion_type=criterion_type,
                )
            )
            annotation_id += 1
            if category == "violation_and_unresolved":
                rows.append(
                    _row(
                        annotation_id,
                        patient_id,
                        trial_id,
                        expert="not enough information",
                    )
                )
                annotation_id += 1

    pairs = group_patient_trial_pairs(rows)
    first = select_pilot_pairs(pairs)
    second = select_pilot_pairs(pairs)

    assert [(pair.patient_id, pair.trial_id) for pair in first] == [
        (pair.patient_id, pair.trial_id) for pair in second
    ]
    assert Counter(pair.category for pair in first) == {
        "clear": 2,
        "unresolved_only": 13,
        "violation_and_unresolved": 4,
        "violation_only": 1,
    }
    assert len({pair.patient_id for pair in first}) == 20


def test_summary_counts_pairs_and_public_baseline_disagreement() -> None:
    first = _row(0, "p1", "t1", expert="included", criterion_type="inclusion")
    second = _row(1, "p1", "t1", expert="not excluded")
    first.gpt4_eligibility = "not enough information"

    summary = summarize_trialgpt_rows([first, second])

    assert summary["criterion_rows"] == 2
    assert summary["patients"] == 1
    assert summary["patient_trial_pairs"] == 1
    assert summary["trials"] == 1
    assert summary["trialgpt_nei_expert_decisive"] == 1


def test_row_rejects_label_from_wrong_criterion_type() -> None:
    with pytest.raises(ValidationError):
        _row(0, "p1", "t1", expert="excluded", criterion_type="inclusion")


def test_loader_rejects_duplicate_annotation_ids(tmp_path: Path) -> None:
    row = _row(0, "p1", "t1")
    source = tmp_path / "duplicate.jsonl"
    source.write_text(
        f"{row.model_dump_json()}\n{row.model_dump_json()}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="not unique"):
        load_trialgpt_rows(source)
