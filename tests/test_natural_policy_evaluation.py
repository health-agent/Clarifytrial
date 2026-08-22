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
        "include_fully_missing": False,
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
    assert current["question_selection_metrics"] == {
        "needed_fact_recall": 1.0,
        "unnecessary_action_count": 0,
        "best_trial_status_recovery_within_budget": 1.0,
        "trial_status_recovery_gap_from_best": 0.0,
        "smallest_best_question_count": 3,
        "smallest_best_question_sets": [[
            "age",
            "albumin_adjusted_serum_calcium",
            "prior_systemic_treatment_count",
        ]],
    }


def test_fully_missing_input_removes_all_pivotal_values(tmp_path):
    destination = tmp_path / "fully-missing.json"

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
        include_fully_missing=True,
    )

    assert result["patient_count"] == 1
    assert result["run_count"] == 10
    payload = json.loads(destination.read_text(encoding="utf-8"))
    fully_missing = [
        item for item in payload["runs"] if item["input_state"] == "fully_missing"
    ]
    assert len(fully_missing) == 5
    initial = next(
        item for item in fully_missing if item["policy_id"] == "no_questions"
    )
    assert initial["initial_pivotal_fact_count"] == 0
    gold_initial = next(
        item
        for item in payload["runs"]
        if item["input_state"] == "gold_structured"
        and item["policy_id"] == "no_questions"
    )
    assert gold_initial["initial_pivotal_fact_count"] == 5
    current = next(
        item
        for item in fully_missing
        if item["policy_id"] == "clarifytrial_exact_coverage_v3"
    )
    assert current["question_selection_metrics"]["needed_fact_recall"] == 1.0
    assert current["question_selection_metrics"]["unnecessary_action_count"] == 0


def test_question_reference_keeps_only_the_globally_smallest_best_sets(tmp_path):
    destination = tmp_path / "smallest-best-sets.json"

    run_natural_policy_evaluation(
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
        patient_ids=["natural-type_2_diabetes-12"],
        include_fully_missing=True,
    )

    payload = json.loads(destination.read_text(encoding="utf-8"))
    run = next(
        item
        for item in payload["runs"]
        if item["input_state"] == "fully_missing"
        and item["policy_id"] == "clarifytrial_exact_coverage_v3"
    )
    metrics = run["question_selection_metrics"]
    assert metrics["smallest_best_question_count"] == 2
    assert metrics["smallest_best_question_sets"] == [
        ["body_mass_index", "hba1c_at_screening"]
    ]
    assert metrics["needed_fact_recall"] == 1.0
    assert metrics["unnecessary_action_count"] == 1
