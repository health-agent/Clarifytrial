from __future__ import annotations

import json
from collections import Counter
from math import factorial
from pathlib import Path

from clarifytrial.datasets.public_protocol_common_facts_known import (
    ACTION_BUDGETS,
    COMMON_FACT_CODES,
    load_common_facts_known_cases,
    run_public_protocol_common_facts_known,
)


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "public_protocol_benchmark_v1"


def _fact_code(fact_id: str) -> str:
    return fact_id.rsplit(":", 1)[-1]


def test_common_hidden_facts_move_to_initial_state_and_leave_question_menu() -> None:
    cases, _, _ = load_common_facts_known_cases(
        trial_set_path=DATA / "trial_set.json",
        patient_pairs_path=DATA / "patient_pairs.json",
    )

    assert len(cases) == 30
    assert {item["split"] for item in cases} == {"heldout"}
    assert Counter(item["missing_fact_count"] for item in cases) == {
        1: 6,
        2: 5,
        3: 11,
        4: 8,
    }
    common = set(COMMON_FACT_CODES)
    for row in cases:
        case = row["case"]
        initial_evidence_ids = {
            item.evidence_id for item in case.initial_patient_state().facts
        }
        menu_ids = {
            item.fact_id for item in case.public_policy_view().available_information
        }
        assert set(row["preprovided_evidence_ids"]) <= initial_evidence_ids
        assert not (set(row["preprovided_fact_ids"]) & menu_ids)
        assert menu_ids == set(row["remaining_fact_ids"])
        assert all(_fact_code(item) not in common for item in menu_ids)
        assert set(row["preprovided_fact_codes"]) <= common


def test_supplemental_run_writes_exact_order_and_paired_outputs(
    tmp_path: Path,
) -> None:
    patient_ids = [
        "source-bladder_cancer-03",
        "source-bladder_cancer-04",
        "source-chronic_pancreatitis-04",
        "source-nephrotic_syndrome-04",
    ]
    output = tmp_path / "run"
    summary_path = run_public_protocol_common_facts_known(
        trial_set_path=DATA / "trial_set.json",
        patient_pairs_path=DATA / "patient_pairs.json",
        output_dir=output,
        patient_ids=patient_ids,
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    rows = json.loads((output / "patient-results.json").read_text(encoding="utf-8"))

    assert summary["heldout_patient_count"] == 4
    assert summary["action_budgets"] == list(ACTION_BUDGETS)
    assert len(summary["policy_metrics"]) == len(ACTION_BUDGETS) * 5
    assert summary["primary_comparisons"][
        "budget_1_rule_minus_random"
    ]["paired_inference"]["trial_status_recovery"]["cluster_unit"] == (
        "base_patient"
    )
    assert summary["primary_comparisons"][
        "normalized_auc_0_5_rule_minus_random"
    ]["budget_range"] == [0, 5]
    assert summary["primary_comparisons"][
        "normalized_auc_0_5_exact_minus_rule"
    ]["paired_inference"]["cluster_unit"] == "base_patient"
    assert summary["exact_rule_equivalence"]["patient_budget_unit_count"] == 24
    transition = summary["direct_budget_0_to_1_transition"]
    assert transition["initial_unresolved_trial_count"] >= transition[
        "resolved_after_one_question_count"
    ]
    assert transition["question_count"] == transition["patients_asked_count"]
    assert (
        transition["disease_groups_with_at_least_one_resolution_count"]
        <= transition["disease_group_count"]
    )
    assert sum(transition["question_category_counts"].values()) == transition[
        "question_count"
    ]
    patient_inference = transition["paired_patient_inference"]
    assert patient_inference["trial_status_match_rate_difference"][
        "cluster_unit"
    ] == "base_patient"
    assert patient_inference["trial_status_match_rate_difference"][
        "pair_count"
    ] == 4

    common = set(COMMON_FACT_CODES)
    for row in rows:
        assert not common.intersection(
            _fact_code(item) for item in row["selected_fact_ids"]
        )
    random_rows = [
        item for item in rows if item["policy_id"] == "random_order_expectation"
    ]
    for row in random_rows:
        assert row["random_permutation_count"] == factorial(
            row["missing_fact_count"]
        )

    for name in (
        "summary.json",
        "patient-results.json",
        "patient-results.csv",
        "policy-metrics.csv",
        "budget-auc.csv",
        "budget-1-paired-patient-differences.csv",
        "budget-1-paired-comparisons.csv",
        "paired-auc-patient-differences.csv",
        "paired-auc-comparisons.csv",
        "exact-rule-comparison.csv",
        "direct-transition-summary.csv",
        "question-category-counts.csv",
        "direct-transition-patient-differences.csv",
    ):
        assert (output / name).is_file()
