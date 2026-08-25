from __future__ import annotations

import json
from pathlib import Path

import pytest

from clarifytrial.reporting import build_architecture_comparison


def _write_run(
    root: Path,
    *,
    name: str,
    architecture: str,
    external_calls: int,
    tokens: int,
    patient_ids: list[str] | None = None,
) -> Path:
    destination = root / name
    destination.mkdir()
    metric = {
        "arm": "clarifytrial",
        "patient_count": 2,
        "trial_count": 4,
        "trial_status_recovery": 1.0,
        "confirmed_rescue_count": 2,
        "rescue_opportunity_count": 2,
        "false_preservation_resolved_count": 1,
        "false_preservation_count": 1,
        "false_candidate_removals": 0,
        "premature_final_confirmations": 0,
        "failed_patient_count": 0,
        "selective_review_count": 0,
        "mechanical_model_correction_count": 0,
        "model_call_count": external_calls,
        "external_model_call_count": external_calls,
        "total_tokens": tokens,
        "total_latency_ms": 10,
        "role_usage": {},
        "cluster_uncertainty": {
            "trial_status_recovery": {"bootstrap_95_ci": {"lower": 1.0, "upper": 1.0}}
        },
    }
    summary = {
        "agent_architecture": architecture,
        "model": "fixture-model",
        "arm_metrics": [metric],
    }
    manifest = {
        "inputs": {"trial_set_sha256": "same", "patient_pairs_sha256": "same"},
        "patient_ids": patient_ids or ["p1", "p2"],
    }
    (destination / "summary.json").write_text(
        json.dumps(summary), encoding="utf-8"
    )
    (destination / "run-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    return destination / "summary.json"


def test_architecture_comparison_records_scope_calls_and_tokens(tmp_path: Path) -> None:
    rules = _write_run(
        tmp_path,
        name="rules",
        architecture="rules_only",
        external_calls=0,
        tokens=0,
    )
    model = _write_run(
        tmp_path,
        name="model",
        architecture="single_judge",
        external_calls=4,
        tokens=1234,
    )

    result = build_architecture_comparison(
        workflow_summary_paths=[rules, model],
        output_dir=tmp_path / "report",
    )

    assert result["same_input_files"] is True
    assert result["same_evaluation_settings"] is True
    assert result["evaluation_scope"] == {
        "patient_input": "standardized_json",
        "criteria": "objective_structured_subset",
        "gold": "frozen_separate_reference_implementation",
        "measures_complete_trial_eligibility": False,
    }
    assert result["rows"][1]["external_model_call_count"] == 4
    assert result["rows"][1]["total_tokens"] == 1234
    report = (tmp_path / "report" / "report.md").read_text(encoding="utf-8")
    assert "완전히 구조화된 조건" in report
    assert "| 4회 | 1,234 |" in report


def test_architecture_comparison_rejects_different_patients(tmp_path: Path) -> None:
    first = _write_run(
        tmp_path,
        name="first",
        architecture="rules_only",
        external_calls=0,
        tokens=0,
    )
    second = _write_run(
        tmp_path,
        name="second",
        architecture="single_judge",
        external_calls=2,
        tokens=10,
        patient_ids=["p1", "different"],
    )

    with pytest.raises(ValueError, match="same inputs, patients, and evaluation settings"):
        build_architecture_comparison(
            workflow_summary_paths=[first, second],
            output_dir=tmp_path / "report",
        )
