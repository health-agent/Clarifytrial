from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from clarifytrial.datasets import build_natural_evaluation_trial_set


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _criterion(nct_id: str, position: int) -> dict[str, object]:
    return {
        "criterion_id": f"{nct_id}:criterion:{position}",
        "group_id": "group_a",
        "nct_id": nct_id,
    }


def test_low_coverage_primary_is_replaced_in_frozen_reserve_order(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    _write_json(
        config_path,
        {
            "protocol_id": "test",
            "selection_seed": "seed",
            "source": "ClinicalTrials.gov API v2",
            "minimum_objective_lines": 2,
            "maximum_objective_lines": 10,
            "allowed_overall_statuses": ["RECRUITING"],
            "groups": [
                {
                    "group_id": "group_a",
                    "label": "질환 A",
                    "query_condition": "condition a",
                    "accepted_condition_terms": ["condition a"],
                    "target_count": 2,
                    "reserve_count": 2,
                }
            ],
        },
    )
    primary_path = tmp_path / "primary.json"
    reserve_path = tmp_path / "reserve.json"
    primary_trials = [
        {"group_id": "group_a", "nct_id": "P1", "title": "P1", "study_url": "p1"},
        {"group_id": "group_a", "nct_id": "P2", "title": "P2", "study_url": "p2"},
    ]
    reserve_trials = [
        {"group_id": "group_a", "nct_id": "R1", "title": "R1", "study_url": "r1"},
        {"group_id": "group_a", "nct_id": "R2", "title": "R2", "study_url": "r2"},
    ]
    _write_json(primary_path, {"trials": primary_trials})
    _write_json(reserve_path, {"reserve_trials": reserve_trials})
    primary_gold_path = tmp_path / "primary-gold.json"
    reserve_gold_path = tmp_path / "reserve-gold.json"
    primary_coverage = [
        {"nct_id": "P1", "meets_minimum": True, "accepted_source_line_count": 2, "criterion_count": 2},
        {"nct_id": "P2", "meets_minimum": False, "accepted_source_line_count": 1, "criterion_count": 1},
    ]
    reserve_coverage = [
        {"nct_id": "R1", "meets_minimum": False, "accepted_source_line_count": 1, "criterion_count": 1},
        {"nct_id": "R2", "meets_minimum": True, "accepted_source_line_count": 2, "criterion_count": 2},
    ]
    _write_json(
        primary_gold_path,
        {
            "source_sha256": hashlib.sha256(primary_path.read_bytes()).hexdigest(),
            "trial_coverage": primary_coverage,
            "criteria": [_criterion("P1", 1), _criterion("P1", 2), _criterion("P2", 1)],
        },
    )
    _write_json(
        reserve_gold_path,
        {
            "source_sha256": hashlib.sha256(reserve_path.read_bytes()).hexdigest(),
            "trial_coverage": reserve_coverage,
            "criteria": [_criterion("R1", 1), _criterion("R2", 1), _criterion("R2", 2)],
        },
    )

    output = tmp_path / "final.json"
    result = build_natural_evaluation_trial_set(
        primary_source_path=primary_path,
        reserve_source_path=reserve_path,
        primary_gold_path=primary_gold_path,
        reserve_gold_path=reserve_gold_path,
        selection_config_path=config_path,
        output_path=output,
    )

    assert result["trial_count"] == 2
    assert result["criterion_count"] == 4
    assert result["replacement_count"] == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert [item["nct_id"] for item in payload["trials"]] == ["P1", "R2"]
    assert payload["trials"][1]["replaced_nct_id"] == "P2"
    assert {item["nct_id"] for item in payload["criteria"]} == {"P1", "R2"}
    with pytest.raises(FileExistsError, match="already exists"):
        build_natural_evaluation_trial_set(
            primary_source_path=primary_path,
            reserve_source_path=reserve_path,
            primary_gold_path=primary_gold_path,
            reserve_gold_path=reserve_gold_path,
            selection_config_path=config_path,
            output_path=output,
        )
