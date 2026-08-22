import json
from pathlib import Path

from clarifytrial.datasets.natural_policy_evaluation import (
    _decision_metrics,
    run_natural_policy_evaluation,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_policy_metrics_keep_candidate_and_confirmation_separate():
    current = [
        {
            "trial_id": "NCT1",
            "candidate_status": "retain",
            "confirmation_status": "not_confirmed",
        }
    ]
    target = [
        {
            "trial_id": "NCT1",
            "candidate_status": "retain",
            "confirmation_status": "confirmed",
        }
    ]

    result = _decision_metrics(current, target)

    assert result["candidate_status_recovery"] == 1
    assert result["confirmation_status_recovery"] == 0
    assert result["trial_status_recovery"] == 0


def test_json_policy_demo_needs_no_natural_structure_result(tmp_path):
    destination = tmp_path / "demo.json"

    result = run_natural_policy_evaluation(
        trial_set_path=(
            REPOSITORY_ROOT
            / "data"
            / "natural_evaluation_v1"
            / "preliminary_trial_set.json"
        ),
        generation_config_path=(
            REPOSITORY_ROOT
            / "configs"
            / "natural_evaluation_patient_generation_v2.json"
        ),
        patient_pairs_path=(
            REPOSITORY_ROOT
            / "data"
            / "natural_evaluation_v2"
            / "preliminary_patient_pairs.json"
        ),
        records_path=(
            REPOSITORY_ROOT
            / "data"
            / "natural_evaluation_v2"
            / "preliminary_natural_records.json"
        ),
        structure_result_paths=[],
        destination=destination,
        action_budget=3,
        splits=["heldout"],
        patient_ids=["natural-breast_cancer-11"],
    )

    assert result["patient_count"] == 1
    assert result["run_count"] == 5
    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["input_mode"] == "standardized_json"
    assert payload["filters"] == {
        "splits": ["heldout"],
        "patient_ids": ["natural-breast_cancer-11"],
    }
    fixed = next(
        item for item in payload["runs"] if item["policy_id"] == "fixed_source_order"
    )
    current = next(
        item
        for item in payload["runs"]
        if item["policy_id"] == "clarifytrial_exact_coverage_v3"
    )
    assert fixed["final_metrics"]["trial_status_recovery"] == 0.6
    assert current["final_metrics"]["trial_status_recovery"] == 1.0
    assert current["selected_fact_codes"] == [
        "age",
        "albumin_adjusted_serum_calcium",
        "prior_systemic_treatment_count",
    ]
