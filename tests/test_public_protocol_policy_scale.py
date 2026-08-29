from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

from clarifytrial.datasets.public_protocol_policy_scale import (
    load_public_protocol_policy_cases,
    run_public_protocol_policy_scale,
)


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "public_protocol_benchmark_v1"


def test_frozen_public_protocol_cases_keep_all_50_patients_and_splits() -> None:
    cases, trial_set, pairs = load_public_protocol_policy_cases(
        trial_set_path=DATA / "trial_set.json",
        patient_pairs_path=DATA / "patient_pairs.json",
    )

    assert trial_set["trial_count"] == 50
    assert pairs["patient_count"] == 50
    assert len(cases) == 50
    assert Counter(item["split"] for item in cases) == {
        "development": 20,
        "heldout": 30,
    }
    assert Counter(item["missing_fact_count"] for item in cases) == {
        1: 10,
        2: 10,
        3: 10,
        5: 20,
    }
    assert len({item["group_id"] for item in cases}) == 10
    assert all(len(item["case"].trials) == 5 for item in cases)
    assert all(item["mean_affected_trials"] >= 1 for item in cases)


def test_small_scale_run_writes_paired_directional_and_shared_results(
    tmp_path: Path,
) -> None:
    patient_ids = [
        "source-bladder_cancer-03",
        "source-bladder_cancer-04",
        "source-pulmonary_fibrosis-03",
        "source-pulmonary_fibrosis-04",
    ]
    summary_path = run_public_protocol_policy_scale(
        trial_set_path=DATA / "trial_set.json",
        patient_pairs_path=DATA / "patient_pairs.json",
        output_dir=tmp_path / "run",
        action_budgets=(0, 1),
        patient_ids=patient_ids,
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary["patient_count"] == 4
    assert summary["heldout_patient_count"] == 4
    assert summary["primary_reporting_scope"].endswith("paired_comparisons_only")
    assert "Never interpret" in summary["split_missingness_warning"]
    assert len(summary["policy_metrics"]) == 2 * 5
    assert len(summary["paired_comparisons"]) == 2 * 4
    assert len(summary["paired_budget_auc"]) == 3
    assert summary["disease_level_sensitivity"]
    assert summary["known_age_sensitivity"]["patient_count"] == 4
    assert (
        summary["known_age_sensitivity"]["fact_code_provided_at_start"]
        == "age_years"
    )
    primary_auc = next(
        item
        for item in summary["paired_budget_auc"]
        if item["candidate_policy_id"] == "clarifytrial_rule_v1"
        and item["baseline_policy_id"] == "random_order_expectation"
    )
    assert primary_auc["patient_count"] == 4
    assert primary_auc["budget_range"] == [0, 1]
    assert primary_auc["paired_inference"]["cluster_unit"] == "base_patient"
    assert summary["shared_degree_effects"]
    rows = [
        json.loads(item)
        for item in (tmp_path / "run" / "patient-results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    five_missing_random = next(
        item
        for item in rows
        if item["patient_id"] == "source-bladder_cancer-04"
        and item["action_budget"] == 1
        and item["policy_id"] == "random_order_expectation"
    )
    assert five_missing_random["random_permutation_count"] == 120
    metric = next(
        item
        for item in summary["policy_metrics"]
        if item["action_budget"] == 1
        and item["policy_id"] == "clarifytrial_rule_v1"
    )
    assert metric["rescue_opportunity_count"] >= metric["confirmed_rescue_count"]
    assert metric["cleanup_opportunity_count"] >= metric["ineligible_cleanup_count"]
    with (tmp_path / "run" / "paired-comparisons.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        comparison_rows = list(csv.DictReader(stream))
    action_row = next(item for item in comparison_rows if item["metric"] == "action_count")
    assert action_row["preferred_direction"] == "lower"
    assert int(action_row["candidate_better_count"]) == int(action_row["losses"])
    recovery_row = next(
        item for item in comparison_rows if item["metric"] == "trial_status_recovery"
    )
    assert recovery_row["preferred_direction"] == "higher"
    assert int(recovery_row["candidate_better_count"]) == int(recovery_row["wins"])
    for name in (
        "summary.json",
        "patient-results.csv",
        "policy-metrics.csv",
        "paired-patient-differences.csv",
        "paired-comparisons.csv",
        "paired-budget-auc-patient-differences.csv",
        "paired-budget-auc-comparisons.csv",
        "disease-level-sensitivity.csv",
        "disease-level-sensitivity-summary.csv",
        "known-age-patient-results.csv",
        "known-age-paired-patient-differences.csv",
        "known-age-paired-comparisons.csv",
        "known-age-policy-metrics.csv",
        "shared-degree-effects.csv",
        "interpretation.md",
    ):
        assert (tmp_path / "run" / name).is_file()
