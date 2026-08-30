from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from clarifytrial.interactive.statistics import stratified_bootstrap_mean


POLICY_LABELS = {
    "no_questions": "추가 확인 없음",
    "authored_order": "입력 순서",
    "random": "각 단계에서 같은 난수 규칙으로 하나를 고르는 무작위 선택",
    "random_order_expectation": "가능한 정보 순서 전체 평균",
    "widest_impact": "현재 영향이 큰 정보 우선",
    "impact_per_cost": "영향 대비 확인 부담",
    "clarifytrial_rule_v1": "여러 시험에 필요한 정보 우선, 같으면 확인 부담 비교",
    "clarifytrial_exact_coverage_v3": "앞으로 확인할 정보 조합 계산",
    "outcome_entropy": "개발자료에서 답이 잘 나뉘는 정보 우선",
    "exact_decision_tree_expected_horizon_1_v1": "개발자료에서 답이 나올 비율을 학습한 방법",
    "exact_decision_tree_expected_v1": "개발자료에서 답이 나올 비율을 학습한 전체 조합 계산",
    "exact_decision_tree_worst_case_horizon_1_v1": "개발자료에서 가장 불리한 답을 가정한 방법",
    "exact_decision_tree_worst_case_v1": "개발자료에서 가장 불리한 답을 가정한 전체 조합 계산",
}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _shared_fact_coverage_row(report_path: Path) -> list[dict[str, Any]]:
    """Flatten the selected-protocol shared-fact report for chart rendering."""

    report = _read(report_path)
    scope = report["scope"]
    overall = report["overall"]
    by_code = overall["shared_criterion_count_by_fact_code"]
    named_codes = {
        "age_years",
        "pregnancy_or_lactation",
        "active_serious_infection",
    }
    other_count = sum(
        int(count) for fact_code, count in by_code.items() if fact_code not in named_codes
    )
    category_total = (
        int(by_code.get("age_years", 0))
        + int(by_code.get("pregnancy_or_lactation", 0))
        + int(by_code.get("active_serious_infection", 0))
        + other_count
    )
    shared_criterion_count = int(
        overall["criteria_whose_fact_is_used_by_at_least_2_trials"]
    )
    if category_total != shared_criterion_count:
        raise ValueError(
            "Shared criterion composition does not match the report total: "
            f"{category_total} != {shared_criterion_count}"
        )
    return [
        {
            **scope,
            **{
                key: value
                for key, value in overall.items()
                if key != "shared_criterion_count_by_fact_code"
            },
            "age_years_shared_criterion_count": int(by_code.get("age_years", 0)),
            "pregnancy_or_lactation_shared_criterion_count": int(
                by_code.get("pregnancy_or_lactation", 0)
            ),
            "active_serious_infection_shared_criterion_count": int(
                by_code.get("active_serious_infection", 0)
            ),
            "other_shared_criterion_count": other_count,
        }
    ]


def _budget_policy_rows(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows = []
    overview = []
    for budget in (1, 2, 3):
        base = root / f"budget-{budget}"
        public = _read(base / "public-patients" / "summary.json")
        grid = _read(base / "public-grid" / "summary.json")
        structural = _read(base / "structural-1800" / "summary.json")
        overview.extend(
            [
                {
                    "suite": "public_patient_profiles",
                    "budget": budget,
                    "independent_unit": "base_patient",
                    "independent_unit_count": public["heldout_patient_count"],
                    "expanded_case_or_state_count": (
                        public["heldout_patient_count"]
                        * public["masks_per_patient"]
                    ),
                    "total_masked_case_count": public["masked_case_count"],
                    "random_order_count_per_masked_case": public[
                        "random_order_count_per_masked_case"
                    ],
                    "random_order_policy_run_count": public[
                        "random_order_policy_run_count"
                    ],
                    "policy_count": public["policy_count"],
                    "runtime_seconds": public["runtime_seconds"],
                    "api_cost_usd": public["estimated_api_cost_usd"],
                },
                {
                    "suite": "public_declared_value_grid",
                    "budget": budget,
                    "independent_unit": "exhaustive_declared_grid",
                    "independent_unit_count": grid["visible_context_count"],
                    "expanded_case_or_state_count": grid[
                        "hidden_value_combination_count"
                    ],
                    "policy_count": grid["policy_count"],
                    "scenario_policy_evaluation_count": grid[
                        "scenario_policy_evaluations"
                    ],
                    "runtime_seconds": grid["runtime_seconds"],
                    "api_cost_usd": grid["estimated_api_cost_usd"],
                },
                {
                    "suite": "synthetic_graph_stress",
                    "budget": budget,
                    "independent_unit": "synthetic_structure",
                    "independent_unit_count": structural["structure_count"],
                    "expanded_case_or_state_count": structural[
                        "structure_state_count"
                    ],
                    "policy_count": structural["policy_count"],
                    "scenario_policy_evaluation_count": structural[
                        "policy_state_evaluation_count"
                    ],
                    "runtime_seconds": structural["runtime_seconds"],
                    "api_cost_usd": structural["estimated_api_cost_usd"],
                },
            ]
        )
        for item in public["policy_metrics"]:
            if item["split"] != "heldout":
                continue
            rows.append(
                {
                    "suite": "public_patient_profiles",
                    "evaluation_distribution": "heldout",
                    "budget": budget,
                    "policy_id": item["policy_id"],
                    "policy_label": POLICY_LABELS.get(
                        item["policy_id"], item["policy_id"]
                    ),
                    "mean_status_match_rate": item["mean_trial_recovery"],
                    "mean_status_matches_out_of_five": item[
                        "mean_final_status_matches_out_of_five"
                    ],
                    "mean_new_status_matches_out_of_five": item[
                        "mean_incremental_status_matches_out_of_five"
                    ],
                    "new_status_matches_per_action": item[
                        "incremental_status_matches_per_action"
                    ],
                    "mean_actions": item["mean_actions"],
                    "mean_route_cost": item["mean_route_cost"],
                }
            )
        for item in grid["policy_metrics"]:
            if item["evaluation_distribution"] not in {
                "heldout_kernel",
                "uniform_grid",
            }:
                continue
            rows.append(
                {
                    "suite": "public_declared_value_grid",
                    "evaluation_distribution": item[
                        "evaluation_distribution"
                    ],
                    "budget": budget,
                    "policy_id": item["policy_id"],
                    "policy_label": POLICY_LABELS.get(
                        item["policy_id"], item["policy_id"]
                    ),
                    "mean_status_match_rate": item["mean_trial_recovery"],
                    "mean_status_matches_out_of_five": item[
                        "mean_final_status_matches_out_of_five"
                    ],
                    "mean_new_status_matches_out_of_five": item[
                        "mean_incremental_status_matches_out_of_five"
                    ],
                    "new_status_matches_per_action": item[
                        "incremental_status_matches_per_action"
                    ],
                    "mean_actions": item["mean_actions"],
                    "mean_route_cost": item["mean_route_cost"],
                }
            )
        for item in structural["policy_metrics"]:
            if item["evaluation_distribution"] not in {
                "similar_heldout",
                "shifted_heldout",
            }:
                continue
            rows.append(
                {
                    "suite": "synthetic_graph_stress",
                    "evaluation_distribution": item[
                        "evaluation_distribution"
                    ],
                    "budget": budget,
                    "policy_id": item["policy_id"],
                    "policy_label": POLICY_LABELS.get(
                        item["policy_id"], item["policy_id"]
                    ),
                    "mean_status_match_rate": item[
                        "expected_trial_recovery"
                    ],
                    "mean_status_matches_out_of_five": item[
                        "mean_final_status_matches_out_of_five"
                    ],
                    "mean_new_status_matches_out_of_five": item[
                        "mean_incremental_status_matches_out_of_five"
                    ],
                    "new_status_matches_per_action": item[
                        "incremental_status_matches_per_action"
                    ],
                    "mean_actions": item["expected_actions"],
                    "mean_route_cost": item["expected_route_cost"],
                }
            )
    return rows, overview


def _subgroup_rows(root: Path) -> list[dict[str, Any]]:
    rows = []
    public_pairs = (
        ("clarifytrial_exact_coverage_v3", "authored_order"),
        ("clarifytrial_exact_coverage_v3", "widest_impact"),
        ("clarifytrial_exact_coverage_v3", "clarifytrial_rule_v1"),
        ("widest_impact", "random_order_expectation"),
        ("clarifytrial_rule_v1", "random_order_expectation"),
    )
    structural_pairs = (
        ("clarifytrial_exact_coverage_v3", "authored_order"),
        ("clarifytrial_exact_coverage_v3", "widest_impact"),
        ("clarifytrial_exact_coverage_v3", "clarifytrial_rule_v1"),
        ("widest_impact", "random"),
        ("clarifytrial_rule_v1", "random"),
    )
    for budget in (1, 2, 3):
        base = root / f"budget-{budget}"
        public = _read(base / "public-patients" / "summary.json")
        structural = _read(base / "structural-1800" / "summary.json")
        public_index = {
            (item["group_id"], item["policy_id"]): item
            for item in public["disease_metrics"]
            if item["split"] == "heldout"
        }
        for group_id in sorted({key[0] for key in public_index}):
            for candidate_id, baseline_id in public_pairs:
                candidate = public_index[(group_id, candidate_id)][
                    "mean_trial_recovery"
                ]
                baseline = public_index[(group_id, baseline_id)][
                    "mean_trial_recovery"
                ]
                rows.append(
                    {
                        "suite": "public_patient_profiles",
                        "subgroup_type": "disease",
                        "subgroup": group_id,
                        "evaluation_distribution": "heldout",
                        "budget": budget,
                        "candidate_policy_id": candidate_id,
                        "baseline_policy_id": baseline_id,
                        "candidate_score": candidate,
                        "baseline_score": baseline,
                        "difference": candidate - baseline,
                    }
                )
        structural_index = {
            (
                item["topology"],
                item["evaluation_distribution"],
                item["policy_id"],
            ): item
            for item in structural["topology_metrics"]
        }
        overlap_by_topology = {
            item["topology"]: item
            for item in structural["topology_overlap_metrics"]
        }
        for topology in sorted({key[0] for key in structural_index}):
            for distribution in ("similar_heldout", "shifted_heldout"):
                for candidate_id, baseline_id in structural_pairs:
                    candidate = structural_index[
                        (topology, distribution, candidate_id)
                    ]["expected_trial_recovery"]
                    baseline = structural_index[
                        (topology, distribution, baseline_id)
                    ]["expected_trial_recovery"]
                    rows.append(
                        {
                            "suite": "synthetic_graph_stress",
                            "subgroup_type": "graph_topology",
                            "subgroup": topology,
                            "evaluation_distribution": distribution,
                            "budget": budget,
                            "candidate_policy_id": candidate_id,
                            "baseline_policy_id": baseline_id,
                            "candidate_score": candidate,
                            "baseline_score": baseline,
                            "difference": candidate - baseline,
                            "criterion_edge_count": overlap_by_topology[
                                topology
                            ]["criterion_edge_count"],
                            "shared_fact_count": overlap_by_topology[
                                topology
                            ]["shared_fact_count"],
                            "all_trial_shared_fact_count": overlap_by_topology[
                                topology
                            ]["all_trial_shared_fact_count"],
                            "max_trials_per_fact": overlap_by_topology[
                                topology
                            ]["max_trials_per_fact"],
                            "max_trial_coverage_fraction": overlap_by_topology[
                                topology
                            ]["max_trial_coverage_fraction"],
                            "shared_fact_edge_fraction": overlap_by_topology[
                                topology
                            ]["shared_fact_edge_fraction"],
                        }
                    )
    return rows


def _ci_rows(root: Path) -> list[dict[str, Any]]:
    rows = []
    for budget in (1, 2, 3):
        base = root / f"budget-{budget}"
        public = _read(base / "public-patients" / "summary.json")
        structural = _read(base / "structural-1800" / "summary.json")
        grid = _read(base / "public-grid" / "summary.json")
        for item in public["paired_heldout"]["core_policy_comparisons"]:
            rows.append(
                {
                    "suite": "public_patient_profiles",
                    "evaluation_distribution": "heldout",
                    "budget": budget,
                    "baseline_policy_id": item["baseline_policy_id"],
                    "mean_difference": item["mean_difference"],
                    "ci_95_lower": item["bootstrap_95_ci"]["lower"],
                    "ci_95_upper": item["bootstrap_95_ci"]["upper"],
                    "exact_sign_p": item["two_sided_exact_sign_test_p"],
                    "wins": item["wins"],
                    "ties": item["ties"],
                    "losses": item["losses"],
                    "cluster_unit": item["cluster_unit"],
                    "cluster_count": item["pair_count"],
                    "inference_scope": "synthetic base-patient composition",
                }
            )
        for item in structural["core_policy_comparisons"]:
            rows.append(
                {
                    "suite": "synthetic_graph_stress",
                    "evaluation_distribution": item[
                        "evaluation_distribution"
                    ],
                    "budget": budget,
                    "baseline_policy_id": item["baseline_policy_id"],
                    "mean_difference": item["mean_difference"],
                    "ci_95_lower": item["bootstrap_95_ci"]["lower"],
                    "ci_95_upper": item["bootstrap_95_ci"]["upper"],
                    "exact_sign_p": item["two_sided_exact_sign_test_p"],
                    "wins": item["wins"],
                    "ties": item["ties"],
                    "losses": item["losses"],
                    "cluster_unit": item["cluster_unit"],
                    "cluster_count": item["pair_count"],
                    "inference_scope": "declared synthetic graph generator",
                }
            )
        for item in grid["comparison"]["core_policy_comparisons"]:
            rows.append(
                {
                    "suite": "public_declared_value_grid",
                    "evaluation_distribution": item[
                        "evaluation_distribution"
                    ],
                    "budget": budget,
                    "baseline_policy_id": item["baseline_policy_id"],
                    "mean_difference": item["mean_recovery_difference"],
                    "wins": item["wins"],
                    "ties": item["ties"],
                    "losses": item["losses"],
                    "cluster_unit": "group_mask_cell",
                    "cluster_count": item["group_mask_count"],
                    "inference_scope": item["inference"],
                }
            )
    return rows


def _simple_random_ci_rows(root: Path) -> list[dict[str, Any]]:
    """Build the primary impact-rule comparison table.

    Public profiles use the exact mean over all 120 possible initial fact
    orders. Structural stress uses a fixed seed and chooses among the currently
    relevant remaining facts at each step. These baselines therefore stay
    separate in the exported table.
    """

    rows = []
    for budget in (1, 2, 3):
        base = root / f"budget-{budget}"
        public = _read(base / "public-patients" / "summary.json")
        structural = _read(base / "structural-1800" / "summary.json")
        for item in public["paired_heldout"][
            "primary_simple_vs_random_comparisons"
        ]:
            rows.append(
                {
                    "suite": "public_patient_profiles",
                    "evaluation_distribution": "heldout",
                    "budget": budget,
                    "candidate_policy_id": item["candidate_policy_id"],
                    "baseline_policy_id": item["baseline_policy_id"],
                    "mean_difference": item["mean_difference"],
                    "ci_95_lower": item["bootstrap_95_ci"]["lower"],
                    "ci_95_upper": item["bootstrap_95_ci"]["upper"],
                    "exact_sign_p": item["two_sided_exact_sign_test_p"],
                    "wins": item["wins"],
                    "ties": item["ties"],
                    "losses": item["losses"],
                    "cluster_unit": item["cluster_unit"],
                    "cluster_count": item["pair_count"],
                    "random_baseline_definition": (
                        "exact mean over all 120 possible initial fact orders"
                    ),
                    "inference_scope": "synthetic base-patient composition",
                }
            )
        for item in structural["primary_simple_vs_random_comparisons"]:
            rows.append(
                {
                    "suite": "synthetic_graph_stress",
                    "evaluation_distribution": item[
                        "evaluation_distribution"
                    ],
                    "budget": budget,
                    "candidate_policy_id": item["candidate_policy_id"],
                    "baseline_policy_id": item["baseline_policy_id"],
                    "mean_difference": item["mean_difference"],
                    "ci_95_lower": item["bootstrap_95_ci"]["lower"],
                    "ci_95_upper": item["bootstrap_95_ci"]["upper"],
                    "exact_sign_p": item["two_sided_exact_sign_test_p"],
                    "wins": item["wins"],
                    "ties": item["ties"],
                    "losses": item["losses"],
                    "cluster_unit": item["cluster_unit"],
                    "cluster_count": item["pair_count"],
                    "random_baseline_definition": (
                        "fixed seed; one random choice among currently relevant remaining facts at each step"
                    ),
                    "inference_scope": "declared synthetic graph generator",
                }
            )
    return rows


def _public_planning_random_ci_rows(root: Path) -> list[dict[str, Any]]:
    rows = []
    for budget in (1, 2, 3):
        summary = _read(
            root / f"budget-{budget}" / "public-patients" / "summary.json"
        )
        for item in summary["paired_heldout"][
            "planning_vs_random_comparisons"
        ]:
            rows.append(
                {
                    "suite": "public_patient_profiles",
                    "evaluation_distribution": "heldout",
                    "budget": budget,
                    "candidate_policy_id": item["candidate_policy_id"],
                    "baseline_policy_id": item["baseline_policy_id"],
                    "mean_difference": item["mean_difference"],
                    "ci_95_lower": item["bootstrap_95_ci"]["lower"],
                    "ci_95_upper": item["bootstrap_95_ci"]["upper"],
                    "exact_sign_p": item["two_sided_exact_sign_test_p"],
                    "wins": item["wins"],
                    "ties": item["ties"],
                    "losses": item["losses"],
                    "cluster_unit": item["cluster_unit"],
                    "cluster_count": item["pair_count"],
                    "random_baseline_definition": (
                        "exact mean over all 120 possible initial fact orders"
                    ),
                    "inference_scope": "synthetic base-patient composition",
                }
            )
    return rows


def _budget_curve_rows(policy_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Summarize recovery across zero to three information checks."""

    by_key = {
        (
            item["suite"],
            item["evaluation_distribution"],
            item["budget"],
            item["policy_id"],
        ): item
        for item in policy_rows
    }
    suite_distributions = sorted(
        {
            (item["suite"], item["evaluation_distribution"])
            for item in policy_rows
        }
    )
    result = []
    for suite, distribution in suite_distributions:
        budget_zero = by_key[(suite, distribution, 1, "no_questions")][
            "mean_status_match_rate"
        ]
        policy_ids = sorted(
            {
                item["policy_id"]
                for item in policy_rows
                if item["suite"] == suite
                and item["evaluation_distribution"] == distribution
            }
        )
        for policy_id in policy_ids:
            scores = [
                budget_zero,
                *[
                    by_key[(suite, distribution, budget, policy_id)][
                        "mean_status_match_rate"
                    ]
                    for budget in (1, 2, 3)
                ],
            ]
            raw_auc = sum(
                (scores[index] + scores[index + 1]) / 2
                for index in range(3)
            )
            result.append(
                {
                    "suite": suite,
                    "evaluation_distribution": distribution,
                    "policy_id": policy_id,
                    "policy_label": POLICY_LABELS.get(policy_id, policy_id),
                    "budget_0_score": scores[0],
                    "budget_1_score": scores[1],
                    "budget_2_score": scores[2],
                    "budget_3_score": scores[3],
                    "raw_auc_over_budget_0_to_3": raw_auc,
                    "normalized_auc": raw_auc / 3,
                    "metric_scope": (
                        "status recovery across zero to three information checks"
                    ),
                }
            )
    return result


def _public_protocol_curve_rows(
    source: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Use the 50-patient, 10-disease protocol evaluation for main figures."""

    summary = _read(source / "summary.json")
    metrics = [
        item
        for item in _read_csv(source / "policy-metrics.csv")
        if item["split"] == "heldout"
    ]
    auc_by_policy = {
        item["policy_id"]: item
        for item in _read_csv(source / "budget-auc.csv")
    }
    by_key = {
        (item["policy_id"], int(item["action_budget"])): item
        for item in metrics
    }
    curve_rows = []
    for policy_id, auc in sorted(auc_by_policy.items()):
        curve_rows.append(
            {
                "suite": "public_patient_profiles",
                "source_protocol_id": summary["protocol_id"],
                "evaluation_distribution": "heldout",
                "total_patient_count": summary["patient_count"],
                "heldout_patient_count": summary["heldout_patient_count"],
                "disease_group_count": summary["disease_group_count"],
                "candidate_trials_per_patient": summary[
                    "candidate_trials_per_patient"
                ],
                "policy_id": policy_id,
                "policy_label": auc["policy_label"],
                **{
                    f"budget_{budget}_score": by_key[
                        (policy_id, budget)
                    ]["mean_trial_status_recovery"]
                    for budget in range(6)
                },
                "mean_trial_status_recovery_normalized_auc": auc[
                    "mean_trial_status_recovery_normalized_auc"
                ],
                "confirmed_rescue_rate_normalized_auc": auc[
                    "confirmed_rescue_rate_normalized_auc"
                ],
                "ineligible_cleanup_rate_normalized_auc": auc[
                    "ineligible_cleanup_rate_normalized_auc"
                ],
                "metric_scope": (
                    "question-order behavior on structured public criteria and "
                    "synthetic patients; not clinical performance"
                ),
            }
        )
    return curve_rows, _read_csv(
        source / "paired-budget-auc-comparisons.csv"
    )


def _public_protocol_efficiency_rows(source: Path) -> list[dict[str, Any]]:
    """Express the held-out question-order results in trials and used actions."""

    metrics = [
        item
        for item in _read_csv(source / "policy-metrics.csv")
        if item["split"] == "heldout"
    ]
    initial_matches = float(
        next(
            item
            for item in metrics
            if item["policy_id"] == "no_questions"
            and int(item["action_budget"]) == 0
        )["mean_final_status_matches_out_of_five"]
    )
    rows = []
    for item in metrics:
        final_matches = float(item["mean_final_status_matches_out_of_five"])
        used_actions = float(item["mean_action_count"])
        new_matches = final_matches - initial_matches
        rows.append(
            {
                "split": "heldout",
                "action_budget": int(item["action_budget"]),
                "policy_id": item["policy_id"],
                "policy_label": POLICY_LABELS.get(
                    item["policy_id"], item["policy_id"]
                ),
                "patient_count": int(item["patient_count"]),
                "candidate_trials_per_patient": 5,
                "mean_status_matches_out_of_five": final_matches,
                "mean_initial_status_matches_out_of_five": initial_matches,
                "mean_new_status_matches_out_of_five": new_matches,
                "mean_actions_actually_used": used_actions,
                "new_status_matches_per_action_used": (
                    new_matches / used_actions if used_actions else 0.0
                ),
                "definition": (
                    "additional trial statuses matched after questions divided "
                    "by the mean number of actions actually used"
                ),
            }
        )
    return sorted(rows, key=lambda row: (row["action_budget"], row["policy_id"]))


def _public_patient_auc(
    root: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compute paired patient-level AUC before averaging or resampling."""

    policies = (
        "random_order_expectation",
        "widest_impact",
        "clarifytrial_rule_v1",
        "clarifytrial_exact_coverage_v3",
        "exact_decision_tree_expected_horizon_1_v1",
    )
    rows_by_budget: dict[int, list[dict[str, Any]]] = {}
    for budget in (1, 2, 3):
        path = root / f"budget-{budget}" / "public-patients" / "case-results.jsonl"
        rows_by_budget[budget] = [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line
        ]
    indexed = {
        (
            budget,
            item["profile_id"],
            item["mask_id"],
            item["policy_id"],
        ): item
        for budget, items in rows_by_budget.items()
        for item in items
        if item["split"] == "heldout"
    }
    heldout = [
        item for item in rows_by_budget[1] if item["split"] == "heldout"
    ]
    profile_groups = {
        item["profile_id"]: item["group_id"] for item in heldout
    }
    masks_by_profile: dict[str, list[str]] = defaultdict(list)
    for item in heldout:
        if item["policy_id"] == "no_questions":
            masks_by_profile[item["profile_id"]].append(item["mask_id"])

    patient_rows = []
    for profile_id, group_id in sorted(profile_groups.items()):
        mask_ids = sorted(set(masks_by_profile[profile_id]))
        if len(mask_ids) != 2:
            raise ValueError(f"expected two masks for {profile_id}")
        budget_zero_values = [
            indexed[(1, profile_id, mask_id, "no_questions")][
                "trial_recovery"
            ]
            for mask_id in mask_ids
        ]
        for budget in (2, 3):
            repeated = [
                indexed[(budget, profile_id, mask_id, "no_questions")][
                    "trial_recovery"
                ]
                for mask_id in mask_ids
            ]
            if repeated != budget_zero_values:
                raise ValueError("budget-zero baseline changed across runs")
        score_zero = mean(budget_zero_values)
        for policy_id in policies:
            scores = [score_zero]
            for budget in (1, 2, 3):
                scores.append(
                    mean(
                        indexed[(budget, profile_id, mask_id, policy_id)][
                            "trial_recovery"
                        ]
                        for mask_id in mask_ids
                    )
                )
            raw_auc = sum(
                (scores[index] + scores[index + 1]) / 2
                for index in range(3)
            )
            patient_rows.append(
                {
                    "group_id": group_id,
                    "profile_id": profile_id,
                    "mask_count": len(mask_ids),
                    "policy_id": policy_id,
                    "policy_label": POLICY_LABELS.get(policy_id, policy_id),
                    "budget_0_score": scores[0],
                    "budget_1_score": scores[1],
                    "budget_2_score": scores[2],
                    "budget_3_score": scores[3],
                    "raw_auc_over_budget_0_to_3": raw_auc,
                    "normalized_auc": raw_auc / 3,
                }
            )

    by_key = {
        (item["profile_id"], item["policy_id"]): item
        for item in patient_rows
    }
    comparison_rows = []
    for candidate_id in policies[1:]:
        differences_by_group: dict[str, list[float]] = defaultdict(list)
        candidate_values = []
        baseline_values = []
        for profile_id, group_id in sorted(profile_groups.items()):
            candidate = by_key[(profile_id, candidate_id)]["normalized_auc"]
            baseline = by_key[
                (profile_id, "random_order_expectation")
            ]["normalized_auc"]
            differences_by_group[group_id].append(candidate - baseline)
            candidate_values.append(candidate)
            baseline_values.append(baseline)
        inference = stratified_bootstrap_mean(
            differences_by_group,
            cluster_unit="base_patient",
        )
        comparison_rows.append(
            {
                "candidate_policy_id": candidate_id,
                "baseline_policy_id": "random_order_expectation",
                "base_patient_count": len(profile_groups),
                "masks_per_patient": 2,
                "candidate_mean_normalized_auc": mean(candidate_values),
                "baseline_mean_normalized_auc": mean(baseline_values),
                **inference,
                "calculation": (
                    "average two masks within each patient at each budget; "
                    "trapezoid AUC over budgets 0,1,2,3; normalize by 3; "
                    "then bootstrap patients within disease"
                ),
            }
        )
    return patient_rows, comparison_rows


def _burden_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    mechanism = summary["mechanism_ablation"]
    hard = mechanism["disallowed_path_filter"]
    constrained_ranking = next(
        item
        for item in mechanism["remaining_feasible_path_ranking"]["comparisons"]
        if item["patient_profile_id"] == "mobility_cost_constrained"
    )

    def stage(
        name: str,
        source: dict[str, Any],
        side: str,
    ) -> dict[str, Any]:
        means = source["metric_means"]
        totals = source["metric_totals"]
        return {
            "stage": name,
            "feasible_information_status_match_rate": means[
                "burden_feasible_trial_status_recovery"
            ][side],
            "mean_pending_trial_count": means["pending_trial_count"][side],
            "pending_trial_total": totals["pending_trial_count"][side],
            "fully_resolved_setting_fraction": means[
                "fully_resolved_setting"
            ][side],
            "fully_resolved_setting_count": totals[
                "fully_resolved_setting"
            ][side],
            "mean_summed_route_delay_hours": means[
                "cumulative_delay_hours"
            ][side],
            "mean_cost_rank": means["cumulative_cost_rank"][side],
            "mean_physical_burden": means[
                "cumulative_physical_burden"
            ][side],
            "new_test_total": totals["new_test_count"][side],
            "additional_visit_total": totals["additional_visit_count"][side],
            "explicit_limit_violation_total": totals[
                "explicit_limit_violations"
            ][side],
            "action_total": totals["action_count"][side],
            "base_patient_count": source["base_patient_count"],
            "setting_pair_count": source["setting_pair_count"],
        }

    return [
        stage("1_exact_fixed_route", hard, "baseline"),
        stage("2_apply_explicit_patient_limits", hard, "candidate"),
        stage("3_rank_remaining_paths_by_patient_preferences", constrained_ranking, "candidate"),
    ]


def _burden_paired_rows(summary: dict[str, Any]) -> list[dict[str, Any]]:
    hard = summary["mechanism_ablation"]["disallowed_path_filter"]
    rows = []
    for metric, inference in hard["paired_inference"].items():
        rows.append(
            {
                "comparison": "apply_explicit_patient_limits_minus_exact_fixed_route",
                "metric": metric,
                "mean_difference": inference["mean_difference"],
                "ci_95_lower": inference["bootstrap_95_ci"]["lower"],
                "ci_95_upper": inference["bootstrap_95_ci"]["upper"],
                "wins": inference["wins"],
                "ties": inference["ties"],
                "losses": inference["losses"],
                "exact_sign_p": inference["two_sided_exact_sign_test_p"],
                "independent_unit": inference["cluster_unit"],
                "independent_unit_count": inference["pair_count"],
                "repeated_setting_pair_count": hard["setting_pair_count"],
                "interpretation": (
                    "환자 한 명을 한 단위로 묶어 비교했다. 80개 설정은 합성 환자 "
                    "20명에게 숨긴 정보와 자료 이용 상황을 반복 적용한 조합이다."
                ),
            }
        )
    return rows


def _route_choice_rows(
    summary: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    route = summary.get("route_choice_evaluation", summary)
    profiles = []
    for item in route["profile_metrics"]:
        mode_counts = item["selected_mode_counts"]
        profiles.append(
            {
                **{
                    key: value
                    for key, value in item.items()
                    if key != "selected_mode_counts"
                },
                "existing_official_result_count": mode_counts.get(
                    "existing_official_result", 0
                ),
                "new_noninvasive_test_count": mode_counts.get(
                    "new_noninvasive_test", 0
                ),
                "scope": route["scope"],
                "same_final_judgment_masked_case_count": route[
                    "same_final_judgment_masked_case_count"
                ],
            }
        )
    comparisons = []
    for comparison in route["paired_comparisons"]:
        for metric, inference in comparison["paired_inference"].items():
            comparisons.append(
                {
                    "candidate_patient_profile_id": comparison[
                        "candidate_patient_profile_id"
                    ],
                    "baseline_patient_profile_id": comparison[
                        "baseline_patient_profile_id"
                    ],
                    "candidate_preference_mode": comparison[
                        "candidate_preference_mode"
                    ],
                    "metric": metric,
                    "mean_difference": inference["mean_difference"],
                    "ci_95_lower": inference["bootstrap_95_ci"]["lower"],
                    "ci_95_upper": inference["bootstrap_95_ci"]["upper"],
                    "wins": inference["wins"],
                    "ties": inference["ties"],
                    "losses": inference["losses"],
                    "exact_sign_p": inference[
                        "two_sided_exact_sign_test_p"
                    ],
                    "cluster_unit": inference["cluster_unit"],
                    "cluster_count": inference["pair_count"],
                    "scope": route["scope"],
                }
            )
    return profiles, comparisons


def _integrated_model_smoke_row(
    live_result_path: Path,
    deterministic_result_path: Path,
) -> list[dict[str, Any]]:
    """Compare one live full-UI run with the matching code-only run."""

    live = _read(live_result_path)
    deterministic = _read(deterministic_result_path)

    def final_statuses(payload: dict[str, Any]) -> list[tuple[str, str, str]]:
        decisions = payload["result"]["screening"]["final_decisions"]
        return sorted(
            (
                item["trial_id"],
                item["candidate_status"],
                item["confirmation_status"],
            )
            for item in decisions
        )

    def question_fact_order(payload: dict[str, Any]) -> list[str]:
        return [
            item["agent_action"]["target_fact_id"]
            for item in payload["result"]["screening"]["action_history"]
        ]

    trace_rows = _read_jsonl(live_result_path.parent / "trace.jsonl")
    live_usage_events = [item["usage"] for item in trace_rows if item.get("usage")]
    corrections = [
        correction
        for item in trace_rows
        if item.get("event") == "model_assessments_replaced"
        for correction in item["output"]["corrections"]
    ]
    structured_skip_event_count = sum(
        item.get("event") == "structured_criteria_applied_without_model"
        for item in trace_rows
    )
    correction_transitions: dict[str, int] = defaultdict(int)
    for correction in corrections:
        transition = (
            f"{correction['model']['clinical_status']}→"
            f"{correction['applied']['clinical_status']}"
        )
        correction_transitions[transition] += 1
    model_ids = sorted({item["model_id"] for item in live_usage_events})
    efforts = sorted({item["effort"] for item in live_usage_events})
    usage = live["result"]["usage"]
    role_usage = usage["by_role"]
    matcher_usage = role_usage.get("matcher_judge", {})
    next_evidence_usage = role_usage.get("next_evidence", {})

    return [
        {
            "source_result_path": str(live_result_path),
            "deterministic_reference_path": str(deterministic_result_path),
            "case_id": live["result"]["screening"]["case_id"],
            "patient_id": live["input"]["patient_id"],
            "synthetic_case_count": 1,
            "candidate_trial_count": len(
                live["result"]["screening"]["final_decisions"]
            ),
            "model_ids": ";".join(model_ids),
            "reasoning_efforts": ";".join(efforts),
            "model_call_count": usage["call_count"],
            "input_tokens": usage["input_tokens"],
            "output_tokens": usage["output_tokens"],
            "thinking_tokens": usage["thinking_tokens"],
            "total_tokens": usage["total_tokens"],
            "total_model_latency_seconds": sum(
                item["latency_ms"] for item in live_usage_events
            )
            / 1_000,
            "matcher_judge_call_count": matcher_usage.get("call_count", 0),
            "matcher_judge_tokens": matcher_usage.get("total_tokens", 0),
            "next_evidence_call_count": next_evidence_usage.get("call_count", 0),
            "next_evidence_tokens": next_evidence_usage.get("total_tokens", 0),
            "structured_rule_correction_count": len(corrections),
            "structured_rule_correction_transitions": ";".join(
                f"{transition}({count})"
                for transition, count in sorted(correction_transitions.items())
            ),
            "structured_rule_corrected_criterion_ids": ";".join(
                correction["criterion_id"] for correction in corrections
            ),
            "structured_model_skip_event_count": structured_skip_event_count,
            "final_trial_statuses_match_code_only": (
                final_statuses(live) == final_statuses(deterministic)
            ),
            "question_fact_order_matches_code_only": (
                question_fact_order(live) == question_fact_order(deterministic)
            ),
            "question_fact_order": ";".join(question_fact_order(live)),
            "final_trial_status_signature": ";".join(
                "|".join(item) for item in final_statuses(live)
            ),
            "independent_unit": "single_synthetic_connectivity_case",
            "independent_unit_count": 1,
            "interpretation": (
                "실제 모델 호출과 사용량 기록이 전체 화면 흐름에 연결되는지 확인한 "
                "합성 환자 한 사례다. 모델 정확도나 비교 성능값이 아니다."
            ),
        }
    ]


def _archived_integrated_model_smoke_rows(
    path: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Load the tracked one-case observation without rerunning an external model."""

    integer_fields = {
        "synthetic_case_count",
        "candidate_trial_count",
        "model_call_count",
        "input_tokens",
        "output_tokens",
        "thinking_tokens",
        "total_tokens",
        "matcher_judge_call_count",
        "matcher_judge_tokens",
        "next_evidence_call_count",
        "next_evidence_tokens",
        "structured_rule_correction_count",
        "structured_model_skip_event_count",
        "independent_unit_count",
    }
    float_fields = {"total_model_latency_seconds"}
    boolean_fields = {
        "final_trial_statuses_match_code_only",
        "question_fact_order_matches_code_only",
    }
    indexed: dict[str, dict[str, Any]] = {}
    for source in _read_csv(path):
        row: dict[str, Any] = {
            key: value for key, value in source.items() if key != "routing_version"
        }
        for key in integer_fields:
            row[key] = int(row[key])
        for key in float_fields:
            row[key] = float(row[key])
        for key in boolean_fields:
            if row[key] not in {"True", "False"}:
                raise ValueError(f"invalid archived boolean {key}: {row[key]!r}")
            row[key] = row[key] == "True"
        indexed[source["routing_version"]] = row
    expected = {"before_code_routing", "after_code_routing"}
    if set(indexed) != expected:
        raise ValueError(
            "archived live-model summary must contain before_code_routing and "
            "after_code_routing"
        )
    return [indexed["before_code_routing"]], [indexed["after_code_routing"]]


def _model_role_routing_change_row(
    before: dict[str, Any],
    after: dict[str, Any],
) -> list[dict[str, Any]]:
    """Summarize the same-case change after code-routed structured criteria."""

    if before["case_id"] != after["case_id"]:
        raise ValueError("Before and after model routing runs use different cases")
    same_trial_statuses = (
        before["final_trial_status_signature"]
        == after["final_trial_status_signature"]
    )
    same_question_order = (
        before["question_fact_order"] == after["question_fact_order"]
    )
    if not same_trial_statuses or not same_question_order:
        raise ValueError(
            "Model role routing changed final trial statuses or question order"
        )

    def reduction(before_value: float, after_value: float) -> float:
        return (
            (before_value - after_value) / before_value
            if before_value
            else 0.0
        )

    return [
        {
            "case_id": before["case_id"],
            "patient_id": before["patient_id"],
            "synthetic_case_count": 1,
            "candidate_trial_count": before["candidate_trial_count"],
            "same_final_trial_statuses": same_trial_statuses,
            "same_question_fact_order": same_question_order,
            "before_model_call_count": before["model_call_count"],
            "after_model_call_count": after["model_call_count"],
            "model_call_reduction_rate": reduction(
                before["model_call_count"], after["model_call_count"]
            ),
            "before_matcher_judge_call_count": before[
                "matcher_judge_call_count"
            ],
            "after_matcher_judge_call_count": after[
                "matcher_judge_call_count"
            ],
            "before_next_evidence_call_count": before[
                "next_evidence_call_count"
            ],
            "after_next_evidence_call_count": after[
                "next_evidence_call_count"
            ],
            "before_total_tokens": before["total_tokens"],
            "after_total_tokens": after["total_tokens"],
            "token_reduction_rate": reduction(
                before["total_tokens"], after["total_tokens"]
            ),
            "before_total_model_latency_seconds": before[
                "total_model_latency_seconds"
            ],
            "after_total_model_latency_seconds": after[
                "total_model_latency_seconds"
            ],
            "model_latency_sum_reduction_rate": reduction(
                before["total_model_latency_seconds"],
                after["total_model_latency_seconds"],
            ),
            "before_structured_rule_correction_count": before[
                "structured_rule_correction_count"
            ],
            "after_structured_rule_correction_count": after[
                "structured_rule_correction_count"
            ],
            "before_structured_model_skip_event_count": before[
                "structured_model_skip_event_count"
            ],
            "after_structured_model_skip_event_count": after[
                "structured_model_skip_event_count"
            ],
            "independent_unit": "single_synthetic_connectivity_case",
            "independent_unit_count": 1,
            "interpretation": (
                "코드로 계산할 수 있는 조건을 조건 판단 모델에 보내지 않도록 역할을 "
                "바꾼 전후 한 사례 비교다. 정확도나 일반 처리시간의 성능 추정이 아니다."
            ),
        }
    ]


def _statistical_unit_audit_rows(
    *,
    common_transition: dict[str, Any],
    structural_summary: dict[str, Any],
    burden_summary: dict[str, Any],
    route_choice_summary: dict[str, Any],
    live_model_smoke: dict[str, Any],
) -> list[dict[str, Any]]:
    """State what each headline count represents before presentation use."""

    route = route_choice_summary.get(
        "route_choice_evaluation", route_choice_summary
    )
    hard = burden_summary["mechanism_ablation"]["disallowed_path_filter"]
    return [
        {
            "analysis": "세 기본 항목 뒤 질문 1회 직접 전이",
            "unit_counted_for_uncertainty": "합성 환자",
            "unit_count": common_transition["patient_count"],
            "repeated_measurement": "환자마다 연결한 시험 5건",
            "repeated_measurement_count": common_transition["trial_pair_count"],
            "headline_denominator": common_transition[
                "initial_unresolved_trial_count"
            ],
            "headline_numerator": common_transition[
                "resolved_after_one_question_count"
            ],
            "plain_scope": (
                "세 기본 항목을 먼저 제공하고도 남은 시험에서 질문, 답 반영과 "
                "재판정이 이어지는지 본 값"
            ),
        },
        {
            "analysis": "세 기본 항목 뒤 질문 순서 비교",
            "unit_counted_for_uncertainty": "합성 환자",
            "unit_count": common_transition["patient_count"],
            "repeated_measurement": "같은 환자의 시험·선택 방법·질문 순서 반복 계산",
            "repeated_measurement_count": common_transition["trial_pair_count"],
            "headline_denominator": common_transition["patient_count"],
            "headline_numerator": "환자 단위 짝지은 차이",
            "plain_scope": (
                "환자 한 명 안의 시험 다섯 건을 먼저 묶고 현재 규칙과 가능한 "
                "정보 순서 전체 평균을 비교"
            ),
        },
        {
            "analysis": "정보 연결 구조 B1~B3",
            "unit_counted_for_uncertainty": "합성 연결 구조",
            "unit_count": structural_summary["structure_count"],
            "repeated_measurement": (
                "같은 구조를 정보 상태·선택 방법·확인 1·2·3회로 반복"
            ),
            "repeated_measurement_count": (
                structural_summary["structure_state_count"]
                * structural_summary["policy_count"]
                * 3
            ),
            "headline_denominator": structural_summary["structure_count"],
            "headline_numerator": "구조별 선택 방법 차이",
            "plain_scope": (
                "정보 공유 모양을 통제한 1,800개 합성 구조에서 질문 순서가 "
                "작동하는 조건을 확인"
            ),
        },
        {
            "analysis": "같은 정보를 얻는 확인 방법 선택",
            "unit_counted_for_uncertainty": "합성 환자",
            "unit_count": route["base_patient_count"],
            "repeated_measurement": "환자당 숨긴 정보 2개·상황 3개와 연속 행동",
            "repeated_measurement_count": route["masked_case_count"],
            "headline_denominator": 85,
            "headline_numerator": 85,
            "plain_scope": (
                "합성 환자 20명에게 정해 둔 상황별 선택 규칙을 반복해 "
                "85번의 확인 방법이 모두 예상대로 골라졌는지 확인"
            ),
        },
        {
            "analysis": "환자가 허용하지 않은 확인 방법 제거",
            "unit_counted_for_uncertainty": "합성 환자",
            "unit_count": hard["base_patient_count"],
            "repeated_measurement": "환자당 숨긴 정보 2개·자료 이용 상황 2개",
            "repeated_measurement_count": hard["setting_pair_count"],
            "headline_denominator": hard["setting_pair_count"],
            "headline_numerator": "같은 설정의 제한 전후 차이",
            "plain_scope": (
                "합성 환자 20명에게 적용한 80개 반복 설정을 환자별로 묶어 비교"
            ),
        },
        {
            "analysis": "실제 모델 전체 화면 연결",
            "unit_counted_for_uncertainty": "합성 환자 한 사례",
            "unit_count": live_model_smoke["independent_unit_count"],
            "repeated_measurement": "같은 합성 사례의 변경 전·후 실행",
            "repeated_measurement_count": 2,
            "headline_denominator": 1,
            "headline_numerator": 1,
            "plain_scope": (
                "실제 모델 호출과 코드 안전장치의 연결 점검이며 정확도나 처리시간 "
                "성능 추정이 아님"
            ),
        },
    ]


def _write_interpretation(
    path: Path,
    policy_rows: list[dict[str, Any]],
    overview: list[dict[str, Any]],
    burden: dict[str, Any],
    patient_auc_comparisons: list[dict[str, Any]],
    subgroup_rows: list[dict[str, Any]],
    route_choice_summary: dict[str, Any],
    public_protocol_curves: list[dict[str, Any]],
    public_protocol_auc_comparisons: list[dict[str, str]],
    public_protocol_efficiency: list[dict[str, Any]],
    public_protocol_pairwise: list[dict[str, str]],
    disease_sensitivity_summary: list[dict[str, str]],
    known_age_metrics: list[dict[str, str]],
    known_age_comparisons: list[dict[str, str]],
    common_facts_known_metrics: list[dict[str, str]],
    common_facts_known_budget1: list[dict[str, str]],
    common_facts_known_auc: list[dict[str, str]],
    common_facts_known_transition: dict[str, str],
    common_facts_known_categories: list[dict[str, str]],
    integrated_model_smoke_before: list[dict[str, Any]],
    integrated_model_smoke_after: list[dict[str, Any]],
    model_role_routing_change: list[dict[str, Any]],
) -> None:
    index = {
        (
            item["suite"],
            item["evaluation_distribution"],
            item["budget"],
            item["policy_id"],
        ): item
        for item in policy_rows
    }
    lines = [
        "# 질문 순서 확대 실험 해석",
        "",
        "평가 대상은 이미 정해진 부족 정보 가운데 무엇을 먼저 확인할지 고르는 순서다. 임상 판단 정확도는 이 실험에서 재지 않았다.",
        "공개 환자 사례, 공개 조건 값 전수 계산, 합성 연결 구조 실험은 서로 다른 자료이므로 결과를 합쳐 하나의 점수로 만들지 않는다.",
    ]
    broad_lines = [
        "",
        "## 민감도 분석: 처음에 정보를 넓게 가린 조건",
        "",
        "나이·임신 또는 수유·활동성 감염까지 일부러 가린 10개 질환 합성 환자 50명에서 확인 순서를 비교했다.",
    ]
    public_index = {item["policy_id"]: item for item in public_protocol_curves}
    for policy_id in (
        "random_order_expectation",
        "authored_order",
        "clarifytrial_rule_v1",
        "clarifytrial_exact_coverage_v3",
    ):
        item = public_index[policy_id]
        scores = ", ".join(
            f"{budget}회 {float(item[f'budget_{budget}_score']):.1%}"
            for budget in range(6)
        )
        broad_lines.append(f"- {item['policy_label']}: {scores}.")
    broad_lines.extend(["", "같은 평가용 환자 30명의 확인 기회 0~5회 곡선을 먼저 합친 대응 비교:"])
    for item in public_protocol_auc_comparisons:
        broad_lines.append(
            f"- {POLICY_LABELS.get(item['candidate_policy_id'], item['candidate_policy_id'])} 대 "
            f"{POLICY_LABELS.get(item['baseline_policy_id'], item['baseline_policy_id'])}: "
            f"{float(item['mean_paired_difference']):+.1%}p "
            f"(95% 범위 {float(item['bootstrap_95_lower']):+.1%}p~"
            f"{float(item['bootstrap_95_upper']):+.1%}p)."
        )
    efficiency_index = {
        (item["action_budget"], item["policy_id"]): item
        for item in public_protocol_efficiency
    }
    one_rule = efficiency_index[(1, "clarifytrial_rule_v1")]
    one_random = efficiency_index[(1, "random_order_expectation")]
    broad_lines.extend(
        [
            "",
            (
                "확인 기회가 한 번일 때 시험 5개 중 판단을 끝낸 수는 영향 우선 "
                f"{one_rule['mean_status_matches_out_of_five']:.2f}개, 무작위 순서 "
                f"{one_random['mean_status_matches_out_of_five']:.2f}개였다."
            ),
            (
                "처음부터 판단할 수 있었던 시험을 빼면 실제 확인 한 번으로 새로 정리된 시험은 "
                f"각각 {one_rule['new_status_matches_per_action_used']:.2f}개와 "
                f"{one_random['new_status_matches_per_action_used']:.2f}개였다."
            ),
        ]
    )
    category_text = ", ".join(
        f"{item['question_category']} {item['question_count']}건"
        for item in common_facts_known_categories
    )
    direct_patient_inference = common_facts_known_transition[
        "paired_patient_inference"
    ]["trial_status_match_rate_difference"]
    lines.extend(
        [
            "",
            "## 발표 주 결과: 세 기본 항목을 받은 뒤 질문 1회",
            "",
            (
                "환자-시험 조합 150개 가운데 처음에 결론이 나지 않은 조합은 "
                f"{common_facts_known_transition['initial_unresolved_trial_count']}건이었다. "
                "질문 한 번 뒤 "
                f"{common_facts_known_transition['resolved_after_one_question_count']}건"
                f"({float(common_facts_known_transition['resolved_after_one_question_rate']):.1%})이 "
                "정리됐다. 참가 조건 충족으로 정리된 시험은 "
                f"{common_facts_known_transition['confirmed_after_one_question_count']}건, "
                "조건 불충족으로 정리된 시험은 "
                f"{common_facts_known_transition['excluded_after_one_question_count']}건이었다."
            ),
            (
                f"환자 {common_facts_known_transition['patients_with_at_least_one_resolution_count']}/30명에서 "
                "한 건 이상 정리됐고, 새로 잘못 확정된 시험은 "
                f"{common_facts_known_transition['unsafe_new_decision_count']}건이었다."
            ),
            (
                "한 건 이상 정리된 환자는 평가에 포함한 질환 "
                f"{common_facts_known_transition['disease_groups_with_at_least_one_resolution_count']}/"
                f"{common_facts_known_transition['disease_group_count']}개에서 나왔다. 한 질환에만 "
                "몰린 결과가 아니었음을 확인하는 범위 정보이며 새 질환으로의 일반화 근거는 아니다."
            ),
            (
                "실제로 선택한 질문 "
                f"{common_facts_known_transition['question_count']}건의 구성은 "
                f"{category_text}이었다."
            ),
            (
                "이 보조 설정에서는 나이·임신 또는 수유·활동성 감염을 먼저 "
                "제공하면서 제외될 시험이 시작 단계에서 모두 정리됐다. 따라서 "
                "남은 미확정 시험은 미리 만든 전체 환자 상태에서 참가 조건을 충족하는 후보였고, "
                "14건은 이 자료 구성에서 확인된 전이지 실제 환자 집단의 참가 증가율이 아니다."
            ),
            (
                "14/22는 세 기본 항목을 먼저 제공하고도 남은 22건에서 질문, 답 반영과 "
                "재판정이 이어지는지 본 값이다. 숫자 범위를 계산할 때는 환자 30명을 한 명씩 "
                "세었고, 한 환자의 시험 다섯 건을 환자 다섯 명처럼 세지 않았다."
            ),
            (
                "환자 안의 시험 다섯 건을 먼저 묶어 비교한 상태 일치율 증가는 "
                f"{direct_patient_inference['mean_difference']:+.1%}p였다. 환자 구성을 "
                f"다시 뽑은 95% 범위는 {direct_patient_inference['bootstrap_95_ci']['lower']:+.1%}p~"
                f"{direct_patient_inference['bootstrap_95_ci']['upper']:+.1%}p였고, "
                f"개선 {direct_patient_inference['wins']}명, 동일 {direct_patient_inference['ties']}명, "
                f"악화 {direct_patient_inference['losses']}명이었다."
            ),
        ]
    )
    action_comparison = next(
        item
        for item in public_protocol_pairwise
        if int(item["action_budget"]) == 5
        and item["candidate_policy_id"] == "clarifytrial_rule_v1"
        and item["baseline_policy_id"] == "random_order_expectation"
        and item["metric"] == "action_count"
    )
    five_rule = efficiency_index[(5, "clarifytrial_rule_v1")]
    five_random = efficiency_index[(5, "random_order_expectation")]
    broad_lines.append(
        "처음 정보를 넓게 숨긴 조건에서 확인 횟수 상한을 5회로 두자 두 방식 모두 시험 5개의 판단을 끝냈다. "
        f"영향 우선은 평균 {five_rule['mean_actions_actually_used']:.2f}회에 멈췄고, "
        f"가능한 순서 전체 평균은 {five_random['mean_actions_actually_used']:.2f}회였다 "
        f"(차이 {float(action_comparison['mean_difference']):+.2f}회, "
        f"환자 구성 95% 범위 {float(action_comparison['bootstrap_95_lower']):+.2f}~"
        f"{float(action_comparison['bootstrap_95_upper']):+.2f}회)."
    )
    disease_checks = [
        item
        for item in disease_sensitivity_summary
        if item["candidate_policy_id"] == "clarifytrial_rule_v1"
        and item["baseline_policy_id"] == "random_order_expectation"
        and (
            (
                item["metric"] == "trial_status_recovery"
                and item["action_budget"] == "1"
            )
            or item["metric"] == "trial_status_recovery_normalized_auc"
        )
    ]
    for item in disease_checks:
        label = (
            "확인 1회"
            if item["metric"] == "trial_status_recovery"
            else "확인 0~5회 곡선 면적"
        )
        broad_lines.append(
            f"- {label}: 선택한 10개 질환 모두에서 무작위 순서보다 높음 "
            f"(질환 방향 부호검정 p={float(item['two_sided_exact_sign_test_p']):.6f})."
        )
    broad_lines.append(
        "질환별 방향은 선택한 10개 질환 안의 민감도 점검이며, 새 질환이나 새 시험군으로의 일반화를 뜻하지 않는다."
    )
    known_age_index = {
        (int(item["action_budget"]), item["policy_id"]): item
        for item in known_age_metrics
    }
    known_age_comparison = next(
        item
        for item in known_age_comparisons
        if int(item["action_budget"]) == 1
        and item["candidate_policy_id"] == "clarifytrial_rule_v1"
        and item["baseline_policy_id"] == "random_order_expectation"
        and item["metric"] == "trial_status_recovery"
    )
    known_age_rule = known_age_index[(1, "clarifytrial_rule_v1")]
    known_age_random = known_age_index[(1, "random_order_expectation")]
    broad_lines.extend(
        [
            "",
            "주평가에서 현재 규칙이 30명 중 29명에게 나이를 먼저 확인했으므로, 나이를 처음부터 제공한 같은 환자들로 민감도 분석을 했다.",
            (
                "남은 정보 한 번을 확인했을 때 무작위 순서는 "
                f"{float(known_age_random['mean_trial_status_recovery']):.1%}, "
                f"영향 우선은 {float(known_age_rule['mean_trial_status_recovery']):.1%}였다. "
                f"차이는 {float(known_age_comparison['mean_difference']):+.1%}p "
                f"(환자 구성 95% 범위 {float(known_age_comparison['bootstrap_95_lower']):+.1%}p~"
                f"{float(known_age_comparison['bootstrap_95_upper']):+.1%}p, "
                f"개선 {known_age_comparison['wins']}명, 동일 {known_age_comparison['ties']}명, "
                f"악화 {known_age_comparison['losses']}명)."
            ),
            "나이를 먼저 제공한 뒤에도 5명에서 차이가 남았지만 평균 차이는 +3.4%p로 줄었다. 주평가의 큰 차이는 공통 나이 항목의 영향을 많이 받았다.",
        ]
    )
    common_metric_index = {
        (int(item["action_budget"]), item["policy_id"]): item
        for item in common_facts_known_metrics
    }
    common_budget1 = next(
        item
        for item in common_facts_known_budget1
        if item["candidate_policy_id"] == "clarifytrial_rule_v1"
        and item["baseline_policy_id"] == "random_order_expectation"
        and item["metric"] == "trial_status_recovery"
    )
    common_auc = next(
        item
        for item in common_facts_known_auc
        if item["candidate_policy_id"] == "clarifytrial_rule_v1"
        and item["baseline_policy_id"] == "random_order_expectation"
    )
    common_rule = common_metric_index[(1, "clarifytrial_rule_v1")]
    common_random = common_metric_index[(1, "random_order_expectation")]
    common_no_question = common_metric_index[(1, "no_questions")]
    lines.extend(
        [
            "",
            "### 질문 순서 자체의 추가 차이",
            "",
            "나이·임신 또는 수유·활동성 감염을 시작 자료에 모두 넣은 뒤 남은 정보만으로 한 번 더 확인했다.",
            (
                "확인 1회 결과는 가능한 순서 전체 평균 "
                f"{float(common_random['mean_trial_status_recovery']):.1%}, 영향 우선 "
                f"{float(common_rule['mean_trial_status_recovery']):.1%}였다. 차이는 "
                f"{float(common_budget1['mean_difference']):+.1%}p "
                f"(95% 범위 {float(common_budget1['bootstrap_95_lower']):+.1%}p~"
                f"{float(common_budget1['bootstrap_95_upper']):+.1%}p, "
                f"개선 {common_budget1['wins']}명, 동일 {common_budget1['ties']}명)."
            ),
            (
                "추가 확인을 하지 않으면 "
                f"{float(common_no_question['mean_trial_status_recovery']):.1%}였다. "
                "세 기본 항목을 받은 뒤에도 질문 한 번으로 남은 판단을 더 정리했지만, "
                "어떤 질문을 먼저 고르는지에 따른 차이는 작았다."
            ),
            (
                "확인 0~5회 전체 곡선 차이는 "
                f"{float(common_auc['mean_paired_difference']):+.2%}p였다. "
                "선택한 공개 조건에서는 세 기본 확인 항목이 주효과의 대부분을 설명했다."
            ),
        ]
    )
    lines.extend(broad_lines)
    lines.extend(
        [
            "",
            "이 수치는 공개 시험의 일부 구조화 조건과 합성 환자로 질문 순서만 검사한 값이며 임상 성능이 아니다.",
            "",
            "## 보조 구조 진단: 세 질환 합성 환자 20명",
            "",
        ]
    )
    for budget in (1, 2, 3):
        lines.append(f"### 확인 기회 {budget}회")
        lines.append("")
        structural_count = next(
            item["independent_unit_count"]
            for item in overview
            if item["suite"] == "synthetic_graph_stress"
            and item["budget"] == budget
        )
        for suite, distribution, label in (
            ("public_patient_profiles", "heldout", "공개 조건 합성 환자 20명"),
            ("public_declared_value_grid", "heldout_kernel", "공개 조건 값 조합 전수 계산"),
            (
                "synthetic_graph_stress",
                "similar_heldout",
                f"합성 연결 구조 {structural_count:,}개",
            ),
        ):
            exact = index[(suite, distribution, budget, "clarifytrial_exact_coverage_v3")]
            widest = index[(suite, distribution, budget, "widest_impact")]
            baseline_id = {
                "public_patient_profiles": "random_order_expectation",
                "public_declared_value_grid": "authored_order",
                "synthetic_graph_stress": "random",
            }[suite]
            baseline = index[(suite, distribution, budget, baseline_id)]
            baseline_label = POLICY_LABELS[baseline_id]
            lines.append(
                f"- {label}: {baseline_label} {baseline['mean_status_match_rate']:.1%}, "
                f"현재 영향 우선 {widest['mean_status_match_rate']:.1%}, "
                f"앞으로 확인할 정보 조합 계산 {exact['mean_status_match_rate']:.1%}."
            )
        lines.append("")
    lines.extend(
        [
            "보조 세 질환 합성 환자 자료의 무작위 기준은 부족 정보 다섯 개의 순서 120개를 모두 계산한 평균이다.",
            "숨은 답의 확률을 쓰지 않고 남은 확인 횟수 안에서 함께 볼 정보 조합을 계산하는 방법은 현재 영향 우선과 따로 비교한다. 개발자료에서 답 가능성을 학습한 의사결정나무 비교군은 별도 결과다.",
            "",
            "## 확인 기회 0~3회 전체 비교",
            "",
            "환자마다 정보를 두 가지로 숨긴 결과를 먼저 평균하고, 확인 기회 0·1·2·3회의 곡선 면적을 계산한 뒤 환자 단위로 다시 뽑았다.",
        ]
    )
    for item in patient_auc_comparisons:
        ci = item["bootstrap_95_ci"]
        lines.append(
            f"- {POLICY_LABELS.get(item['candidate_policy_id'], item['candidate_policy_id'])}: "
            f"무작위 순서보다 {item['mean_difference']:+.1%}p, "
            f"환자 구성 95% 범위 {ci['lower']:+.1%}p~{ci['upper']:+.1%}p "
            f"(개선 {item['wins']}명, 동일 {item['ties']}명, 악화 {item['losses']}명)."
        )
    lines.extend(
        [
            "",
            "## 정보 공유 구조에 따른 차이",
            "",
            "아래 값은 같은 정보가 여러 시험에 연결될 때 질문 순서가 만드는 구조적 차이를 나타낸다. 임상 정확도에는 해당하지 않는다.",
            (
                "숫자 범위를 계산할 때는 합성 연결 구조 1,800개를 하나씩 세었다. "
                "환자 1,800명의 결과가 아니다. "
                "같은 구조를 확인 횟수 1·2·3회에 반복 적용했으므로 예산별 결과도 서로 다른 "
                "환자 표본으로 세지 않는다."
            ),
        ]
    )
    subgroup_index = {
        (
            item["budget"],
            item["subgroup"],
            item["candidate_policy_id"],
        ): item
        for item in subgroup_rows
        if item["suite"] == "synthetic_graph_stress"
        and item["evaluation_distribution"] == "similar_heldout"
        and item["baseline_policy_id"] == "random"
        and item["subgroup"] in {"fully_shared", "fully_separated"}
    }
    for budget in (1, 2, 3):
        for candidate_id in ("widest_impact", "clarifytrial_rule_v1"):
            fully_shared = subgroup_index[(budget, "fully_shared", candidate_id)]
            fully_separated = subgroup_index[
                (budget, "fully_separated", candidate_id)
            ]
            lines.append(
                f"- 확인 {budget}회, {POLICY_LABELS[candidate_id]}: 모든 시험이 한 정보를 공유할 때 "
                f"무작위 대비 {fully_shared['difference']:+.1%}p, "
                f"시험마다 필요한 정보가 완전히 다를 때 {fully_separated['difference']:+.1%}p."
            )
    shifted_scope = {
        item["subgroup"]: item
        for item in subgroup_rows
        if item["suite"] == "synthetic_graph_stress"
        and item["evaluation_distribution"] == "shifted_heldout"
        and int(item["budget"]) == 1
        and item["candidate_policy_id"] == "clarifytrial_rule_v1"
        and item["baseline_policy_id"] == "random"
        and item["subgroup"] in {"fully_shared", "fully_separated", "chain"}
    }
    exact_over_rule = []
    chain_exact_over_rule = []
    for distribution in ("similar_heldout", "shifted_heldout"):
        for budget in (2, 3):
            exact = index[
                (
                    "synthetic_graph_stress",
                    distribution,
                    budget,
                    "clarifytrial_exact_coverage_v3",
                )
            ]
            rule = index[
                (
                    "synthetic_graph_stress",
                    distribution,
                    budget,
                    "clarifytrial_rule_v1",
                )
            ]
            exact_over_rule.append(
                exact["mean_status_match_rate"] - rule["mean_status_match_rate"]
            )
            chain_row = next(
                item
                for item in subgroup_rows
                if item["suite"] == "synthetic_graph_stress"
                and item["evaluation_distribution"] == distribution
                and int(item["budget"]) == budget
                and item["subgroup"] == "chain"
                and item["candidate_policy_id"] == "clarifytrial_exact_coverage_v3"
                and item["baseline_policy_id"] == "clarifytrial_rule_v1"
            )
            chain_exact_over_rule.append(chain_row["difference"])
    lines.extend(
        [
            (
                "위 수치는 개발 때와 같은 답 분포에서 각 단계마다 현재 남은 정보 중 같은 난수 규칙으로 하나를 고르는 무작위 선택과 비교한 값이다. "
                "답 분포를 바꾼 확인 1회 평가에서는 완전 공유 "
                f"{shifted_scope['fully_shared']['difference']:+.1%}p, 완전 분리 "
                f"{shifted_scope['fully_separated']['difference']:+.1%}p, 사슬 "
                f"{shifted_scope['chain']['difference']:+.1%}p였다."
            ),
            (
                "두 합성 답 분포를 함께 보면, 확인을 두세 번 허용하고 앞으로 확인할 정보 조합을 계산한 방법은 현재 규칙보다 "
                f"전체 구조 평균 {min(exact_over_rule):+.1%}p~{max(exact_over_rule):+.1%}p, "
                f"사슬 구조 {min(chain_exact_over_rule):+.1%}p~{max(chain_exact_over_rule):+.1%}p 높았다. "
                "사슬형 연결에서는 여러 단계를 함께 계산하는 방법이 첫 질문의 약점을 줄였다."
            ),
        ]
    )
    lines.extend(["", "## 기본 질문 순서 결정", ""])
    grid_parts = []
    structure_parts = []
    for budget in (1, 2, 3):
        grid_rule = index[
            (
                "public_declared_value_grid",
                "heldout_kernel",
                budget,
                "clarifytrial_rule_v1",
            )
        ]
        grid_widest = index[
            (
                "public_declared_value_grid",
                "heldout_kernel",
                budget,
                "widest_impact",
            )
        ]
        structure_rule = index[
            (
                "synthetic_graph_stress",
                "similar_heldout",
                budget,
                "clarifytrial_rule_v1",
            )
        ]
        structure_widest = index[
            (
                "synthetic_graph_stress",
                "similar_heldout",
                budget,
                "widest_impact",
            )
        ]
        grid_parts.append(
            f"{budget}회 회복 {grid_rule['mean_status_match_rate'] - grid_widest['mean_status_match_rate']:+.2%}p, "
            f"합성 확인 부담 점수 {1 - grid_rule['mean_route_cost'] / grid_widest['mean_route_cost']:.1%} 감소"
        )
        structure_parts.append(
            f"{budget}회 회복 {structure_rule['mean_status_match_rate'] - structure_widest['mean_status_match_rate']:+.2%}p, "
            f"합성 확인 부담 점수 {1 - structure_rule['mean_route_cost'] / structure_widest['mean_route_cost']:.1%} 감소"
        )
    lines.extend(
        [
            "현재 영향 범위만 따르는 방식과, 영향 범위가 같을 때 확인 부담까지 보는 방식을 비교했다.",
            "- 공개 조건 값 전수 계산: " + "; ".join(grid_parts) + ".",
            "- 합성 연결 구조 1,800개: " + "; ".join(structure_parts) + ".",
            (
                "회복 차이는 예산에 따라 방향이 바뀌었고 합성 연결 구조에서는 0.2%포인트 안이었다. "
                "확인 부담은 모든 횟수에서 낮았으므로, 영향 범위를 먼저 보고 점수가 같을 때 부담을 가르는 "
                "규칙을 기본값으로 둔다."
            ),
            (
                "숨은 답 분포를 쓰지 않고 앞으로 확인할 정보 조합을 계산하는 방법과 개발자료의 답 분포를 쓰는 의사결정나무는 "
                "비교·연구용 선택지로 남긴다."
            ),
            "",
        ]
    )
    hard = burden["mechanism_ablation"]["disallowed_path_filter"]
    ranking = burden["mechanism_ablation"]["remaining_feasible_path_ranking"]
    route_choice = route_choice_summary.get(
        "route_choice_evaluation", route_choice_summary
    )
    route_profile = {
        item["patient_profile_id"]: item
        for item in route_choice["profile_metrics"]
    }
    pending_inference = hard["paired_inference"]["pending_trial_count"]
    resolved_inference = hard["paired_inference"]["fully_resolved_setting"]
    lines.extend(
        [
            "## 환자 제한과 부담 순서",
            "",
            (
                "환자가 명시적으로 금지한 경로를 제거하자 새 검사 제안은 "
                f"{hard['metric_totals']['new_test_count']['baseline']}회에서 "
                f"{hard['metric_totals']['new_test_count']['candidate']}회로, 추가 방문은 "
                f"{hard['metric_totals']['additional_visit_count']['baseline']}회에서 "
                f"{hard['metric_totals']['additional_visit_count']['candidate']}회로 줄었다."
            ),
            (
                "환자가 허용한 방법으로 확인할 수 있는 결과를 기준으로 한 일치율은 "
                f"{hard['metric_means']['burden_feasible_trial_status_recovery']['baseline']:.1%}에서 "
                f"{hard['metric_means']['burden_feasible_trial_status_recovery']['candidate']:.1%}로 바뀌었다."
            ),
            (
                "대신 아직 결론이 나지 않은 시험은 설정당 평균 "
                f"{hard['metric_means']['pending_trial_count']['baseline']:.2f}개에서 "
                f"{hard['metric_means']['pending_trial_count']['candidate']:.2f}개로 늘었고, "
                "시험 5개의 판단을 모두 끝낸 설정은 "
                f"{hard['metric_totals']['fully_resolved_setting']['baseline']:.0f}/80개에서 "
                f"{hard['metric_totals']['fully_resolved_setting']['candidate']:.0f}/80개로 줄었다."
            ),
            (
                "합성 환자 20명에게 정보를 두 가지로 숨기고 자료 이용 상황 두 가지를 반복해 "
                "80개 설정을 만들었다. 환자 단위 대응 재추출에서 "
                f"미확정 시험 증가는 평균 {pending_inference['mean_difference']:+.2f}개 "
                f"(95% 범위 {pending_inference['bootstrap_95_ci']['lower']:+.2f}~"
                f"{pending_inference['bootstrap_95_ci']['upper']:+.2f}), 모든 시험을 정리한 "
                f"설정 비율 변화는 {resolved_inference['mean_difference']:+.1%}p "
                f"(95% 범위 {resolved_inference['bootstrap_95_ci']['lower']:+.1%}p~"
                f"{resolved_inference['bootstrap_95_ci']['upper']:+.1%}p)였다."
            ),
            (
                "현재 합성 확인 방법 표에서는 영향도가 같은 방법이 여러 개 남지 않아, "
                "허용하지 않은 방법을 제거한 뒤 환자 성향으로 순서를 바꾸는 효과를 따로 측정할 수 없었다."
            ),
            "",
            "통제된 경로 선택 평가에서는 같은 합성 답을 주는 두 방법을 모든 부족 정보에 붙였다.",
            (
                "시간이 급한 설정은 빠른 새 검사를 골라 새 검사와 추가 방문이 각각 "
                f"{route_profile['time_urgent']['new_test_total']}회 발생했고, "
                "부담을 줄이는 설정과 이동·비용 제한 설정은 기존 공식 결과 회수 경로를 골라 "
                "새 검사와 추가 방문이 0회였다."
            ),
            (
                "세 설정은 환자 정보를 숨긴 40개 사례에서 모두 같은 최종 판단과 같은 정보 순서를 유지했다. "
                f"경로별 대기시간을 더한 합성 값은 {route_profile['low_extra_burden']['mean_summed_route_delay_hours']:.1f}시간과 "
                f"{route_profile['time_urgent']['mean_summed_route_delay_hours']:.1f}시간이었다."
            ),
            (
                "각 85회는 합성 환자 20명에게 정보를 두 가지로 숨긴 40개 사례에서 생긴 "
                "경로 선택 행동의 합이다. 환자 85명의 결과가 아니다. 환자 상황별로 정해 둔 "
                "선택 규칙대로 85번 모두 골랐는지 확인한 값이다."
            ),
            "",
            "## 실제 모델 호출 경로 한 사례",
            "",
            (
                "같은 합성 환자와 시험 다섯 건을 두 번 실행해 모델 역할 배치를 바꿨다. "
                "처음에는 구조화 규칙으로 계산할 조건도 조건 판단 모델에 보냈고, 개선 뒤에는 "
                "코드로 계산할 수 있는 조건을 모델 호출 전에 끝냈다. 질문 문장 역할만 모델에 남겼다."
            ),
            (
                f"모델 호출은 {integrated_model_smoke_before[0]['model_call_count']}회에서 "
                f"{integrated_model_smoke_after[0]['model_call_count']}회로 "
                f"{model_role_routing_change[0]['model_call_reduction_rate']:.1%} 줄었다. 조건 판단 모델은 "
                f"{integrated_model_smoke_before[0]['matcher_judge_call_count']}회에서 "
                f"{integrated_model_smoke_after[0]['matcher_judge_call_count']}회로 줄었고, 다음 확인 "
                f"문장 역할은 {integrated_model_smoke_after[0]['next_evidence_call_count']}회 남았다."
            ),
            (
                f"전체 토큰은 {integrated_model_smoke_before[0]['total_tokens']:,}에서 "
                f"{integrated_model_smoke_after[0]['total_tokens']:,}으로 "
                f"{model_role_routing_change[0]['token_reduction_rate']:.1%} 줄었다. 모델 응답시간을 "
                f"더한 값은 {integrated_model_smoke_before[0]['total_model_latency_seconds']:.3f}초에서 "
                f"{integrated_model_smoke_after[0]['total_model_latency_seconds']:.3f}초로 "
                f"{model_role_routing_change[0]['model_latency_sum_reduction_rate']:.1%} 줄었다. "
                "한 사례의 호출 시간 합이며 전체 프로그램 실행시간이나 평균 처리시간이 아니다."
            ),
            (
                "질문 정보 순서와 최종 시험 다섯 건의 상태는 전후와 각각의 코드 전용 실행에서 "
                f"같았다. 개선 전에는 모델 판정 {integrated_model_smoke_before[0]['structured_rule_correction_count']}건을 "
                "구조화 규칙이 고쳤고, 개선 뒤에는 "
                f"구조화 조건을 모델에서 건너뛴 단계가 {integrated_model_smoke_after[0]['structured_model_skip_event_count']}회, "
                "사후 교정은 0건이었다. 코드로 계산 가능한 조건을 모델에 보내지 않은 한 사례의 "
                "역할 배치와 사용량 개선이며 정확도 향상이나 일반 성능값이 아니다."
            ),
            "",
            "## 범위",
            "",
            "- 발표 주 결과는 숫자 범위를 계산할 때 평가용 환자 30명을 한 명씩 세었다. 보조 세 질환 결과도 환자 20명을 한 명씩 세었으며, 정보를 두 가지로 숨긴 결과를 40명의 새 환자로 세지 않았다.",
            "- 공개 값 전수 계산은 선언한 값 공간의 전체 결과이므로 표본 신뢰구간을 붙이지 않는다.",
            "- 구조 실험의 신뢰구간은 선언한 합성 연결 유형 안에서 구조를 다시 뽑은 구성 민감도다.",
            "- 질문 순서 확대 실험은 모두 코드 계산이며 외부 모델 호출과 API 비용은 0이다. 실제 모델 호출은 별도의 합성 환자 한 사례 연결 점검으로 분리했다.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--burden-summary", required=True, type=Path)
    parser.add_argument("--route-choice-summary", type=Path)
    parser.add_argument("--public-protocol-scale", type=Path)
    parser.add_argument("--common-facts-known", type=Path)
    parser.add_argument("--shared-fact-report", type=Path)
    parser.add_argument("--live-model-smoke-before-result", type=Path)
    parser.add_argument("--live-model-smoke-after-result", type=Path)
    parser.add_argument("--deterministic-smoke-before-result", type=Path)
    parser.add_argument("--deterministic-smoke-after-result", type=Path)
    parser.add_argument(
        "--archived-live-model-smoke-summary",
        type=Path,
        help=(
            "reuse the tracked one-case external-model observation instead of "
            "requiring ignored raw run directories"
        ),
    )
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    policy_rows, overview = _budget_policy_rows(args.run_root)
    public_protocol_scale = (
        args.public_protocol_scale
        or args.run_root.parent / "public-protocol-policy-scale-20260830"
    )
    common_facts_known = (
        args.common_facts_known
        or args.run_root.parent
        / "public-protocol-common-facts-known-rebuild-v2"
    )
    public_protocol_curves, public_protocol_auc_comparisons = (
        _public_protocol_curve_rows(public_protocol_scale)
    )
    public_protocol_efficiency = _public_protocol_efficiency_rows(
        public_protocol_scale
    )
    public_protocol_summary = _read(public_protocol_scale / "summary.json")
    public_protocol_random_rows = [
        item
        for item in _read_csv(public_protocol_scale / "patient-results.csv")
        if item["split"] == "heldout"
        and int(item["action_budget"]) == 1
        and item["policy_id"] == "random_order_expectation"
    ]
    random_order_counts: dict[int, int] = defaultdict(int)
    for item in public_protocol_random_rows:
        random_order_counts[int(float(item["random_permutation_count"]))] += 1
    overview.insert(
        0,
        {
            "suite": "public_protocol_patient_profiles",
            "budget": "0-5",
            "independent_unit": "heldout_base_patient",
            "independent_unit_count": public_protocol_summary[
                "heldout_patient_count"
            ],
            "total_synthetic_patient_count": public_protocol_summary[
                "patient_count"
            ],
            "trial_set_path": public_protocol_summary["trial_set_path"],
            "patient_pairs_path": public_protocol_summary["patient_pairs_path"],
            "missing_fact_count_distribution": json.dumps(
                public_protocol_summary["missing_fact_count_distribution"],
                ensure_ascii=False,
                sort_keys=True,
            ),
            "disease_group_count": public_protocol_summary[
                "disease_group_count"
            ],
            "expanded_case_or_state_count": (
                public_protocol_summary["heldout_patient_count"]
                * public_protocol_summary["candidate_trials_per_patient"]
            ),
            "policy_count": len(public_protocol_summary["policy_ids"]),
            "random_order_enumeration": "; ".join(
                f"{patient_count} patients x {order_count} orders"
                for order_count, patient_count in sorted(random_order_counts.items())
            ),
            "random_order_policy_run_count_per_budget": sum(
                order_count * patient_count
                for order_count, patient_count in random_order_counts.items()
            ),
            "model_calls": public_protocol_summary["model_calls"],
            "model_tokens": public_protocol_summary["model_tokens"],
            "runtime_seconds": public_protocol_summary["runtime_seconds"],
            "api_cost_usd": 0.0,
            "scope": public_protocol_summary["evaluation_scope"],
        },
    )
    disease_sensitivity_summary = _read_csv(
        public_protocol_scale / "disease-level-sensitivity-summary.csv"
    )
    known_age_metrics = _read_csv(
        public_protocol_scale / "known-age-policy-metrics.csv"
    )
    known_age_comparisons = _read_csv(
        public_protocol_scale / "known-age-paired-comparisons.csv"
    )
    public_protocol_pairwise = _read_csv(
        public_protocol_scale / "paired-comparisons.csv"
    )
    common_facts_known_metrics = _read_csv(
        common_facts_known / "policy-metrics.csv"
    )
    common_facts_known_budget1 = _read_csv(
        common_facts_known / "budget-1-paired-comparisons.csv"
    )
    common_facts_known_auc = _read_csv(
        common_facts_known / "paired-auc-comparisons.csv"
    )
    common_facts_known_summary = _read(
        common_facts_known / "summary.json"
    )
    common_facts_known_transition_csv = _read_csv(
        common_facts_known / "direct-transition-summary.csv"
    )[0]
    common_facts_known_transition = common_facts_known_summary[
        "direct_budget_0_to_1_transition"
    ]
    common_facts_known_patient_differences = _read_csv(
        common_facts_known / "direct-transition-patient-differences.csv"
    )
    common_facts_known_categories = _read_csv(
        common_facts_known / "question-category-counts.csv"
    )
    overview.insert(
        1,
        {
            "suite": "public_protocol_common_facts_known_sensitivity",
            "budget": "0-5",
            "independent_unit": "heldout_base_patient",
            "independent_unit_count": common_facts_known_summary[
                "heldout_patient_count"
            ],
            "disease_group_count": common_facts_known_summary[
                "disease_group_count"
            ],
            "expanded_case_or_state_count": (
                common_facts_known_summary["heldout_patient_count"] * 5
            ),
            "preprovided_fact_codes": "; ".join(
                common_facts_known_summary["common_fact_codes_preprovided"]
            ),
            "preprovided_fact_count_distribution": json.dumps(
                common_facts_known_summary[
                    "preprovided_fact_count_distribution"
                ],
                ensure_ascii=False,
                sort_keys=True,
            ),
            "remaining_hidden_fact_count_distribution": json.dumps(
                common_facts_known_summary[
                    "remaining_hidden_fact_count_distribution"
                ],
                ensure_ascii=False,
                sort_keys=True,
            ),
            "model_calls": common_facts_known_summary["model_calls"],
            "model_tokens": common_facts_known_summary["model_tokens"],
            "scope": common_facts_known_summary["evaluation_scope"],
        },
    )
    patient_auc_rows, patient_auc_comparisons = _public_patient_auc(
        args.run_root
    )
    burden = _read(args.burden_summary)
    route_choice_summary = _read(
        args.route_choice_summary
        or args.run_root / "route-choice-controlled" / "summary.json"
    )
    route_choice_profiles, route_choice_comparisons = _route_choice_rows(
        route_choice_summary
    )
    shared_fact_coverage = _shared_fact_coverage_row(
        args.shared_fact_report
        or args.run_root.parent
        / "public-protocol-shared-facts-v1"
        / "shared-fact-report.json"
    )
    if args.archived_live_model_smoke_summary:
        (
            integrated_model_smoke_before,
            integrated_model_smoke_after,
        ) = _archived_integrated_model_smoke_rows(
            args.archived_live_model_smoke_summary
        )
    else:
        integrated_model_smoke_before = _integrated_model_smoke_row(
            args.live_model_smoke_before_result
            or args.run_root.parent
            / "presentation-live-model-smoke-20260830"
            / "result.json",
            args.deterministic_smoke_before_result
            or args.run_root.parent
            / "presentation-deterministic-smoke-20260830"
            / "result.json",
        )
        integrated_model_smoke_after = _integrated_model_smoke_row(
            args.live_model_smoke_after_result
            or args.run_root.parent
            / "presentation-live-model-smoke-code-routed-20260830"
            / "result.json",
            args.deterministic_smoke_after_result
            or args.run_root.parent
            / "presentation-deterministic-smoke-code-routed-20260830"
            / "result.json",
        )
    model_role_routing_change = _model_role_routing_change_row(
        integrated_model_smoke_before[0],
        integrated_model_smoke_after[0],
    )
    structural_summary_for_audit = _read(
        args.run_root / "budget-1" / "structural-1800" / "summary.json"
    )
    _write_csv(args.output / "budget_policy_scores.csv", policy_rows)
    _write_csv(
        args.output / "budget_curve_auc.csv",
        public_protocol_curves,
    )
    _write_csv(
        args.output / "diagnostic_budget_curve_auc.csv",
        _budget_curve_rows(policy_rows),
    )
    _write_csv(
        args.output / "diagnostic_three_disease_patient_budget_auc.csv",
        patient_auc_rows,
    )
    _write_csv(
        args.output / "diagnostic_three_disease_patient_auc_comparisons.csv",
        patient_auc_comparisons,
    )
    _write_json(
        args.output / "diagnostic_three_disease_patient_auc_summary.json",
        {
            "metric": "normalized trapezoid AUC over budgets 0 to 3",
            "independent_unit": "base_patient",
            "base_patient_count": 20,
            "masks_per_patient": 2,
            "comparisons": patient_auc_comparisons,
        },
    )
    _write_csv(
        args.output / "public_protocol_paired_budget_auc.csv",
        public_protocol_auc_comparisons,
    )
    _write_csv(
        args.output / "public_protocol_paired_budget_comparisons.csv",
        public_protocol_pairwise,
    )
    _write_csv(
        args.output / "public_protocol_missing_fact_effects.csv",
        _read_csv(public_protocol_scale / "heldout-missing-fact-effects.csv"),
    )
    _write_csv(
        args.output / "public_protocol_policy_metrics.csv",
        _read_csv(public_protocol_scale / "policy-metrics.csv"),
    )
    _write_csv(
        args.output / "public_protocol_question_efficiency.csv",
        public_protocol_efficiency,
    )
    _write_csv(
        args.output / "public_protocol_shared_degree_effects.csv",
        _read_csv(public_protocol_scale / "shared-degree-effects.csv"),
    )
    _write_csv(
        args.output / "public_protocol_shared_degree_effect_contrasts.csv",
        _read_csv(public_protocol_scale / "shared-degree-effect-contrasts.csv"),
    )
    _write_csv(
        args.output / "public_protocol_disease_level_sensitivity.csv",
        _read_csv(public_protocol_scale / "disease-level-sensitivity.csv"),
    )
    _write_csv(
        args.output / "public_protocol_disease_level_sensitivity_summary.csv",
        disease_sensitivity_summary,
    )
    _write_csv(
        args.output / "public_protocol_known_age_policy_metrics.csv",
        known_age_metrics,
    )
    _write_csv(
        args.output / "public_protocol_known_age_paired_comparisons.csv",
        known_age_comparisons,
    )
    _write_csv(
        args.output / "public_protocol_common_facts_known_policy_metrics.csv",
        common_facts_known_metrics,
    )
    _write_csv(
        args.output / "public_protocol_common_facts_known_budget1.csv",
        common_facts_known_budget1,
    )
    _write_csv(
        args.output / "public_protocol_common_facts_known_auc.csv",
        common_facts_known_auc,
    )
    _write_csv(
        args.output
        / "public_protocol_common_facts_known_direct_transition.csv",
        [common_facts_known_transition_csv],
    )
    _write_csv(
        args.output
        / "public_protocol_common_facts_known_patient_differences.csv",
        common_facts_known_patient_differences,
    )
    _write_csv(
        args.output / "public_protocol_common_facts_known_question_categories.csv",
        common_facts_known_categories,
    )
    _write_csv(args.output / "experiment_overview.csv", overview)
    subgroup_rows = _subgroup_rows(args.run_root)
    _write_csv(args.output / "subgroup_policy_differences.csv", subgroup_rows)
    _write_csv(
        args.output / "simple_vs_random_subgroups.csv",
        [
            item
            for item in subgroup_rows
            if item["candidate_policy_id"]
            in {"widest_impact", "clarifytrial_rule_v1"}
            and item["baseline_policy_id"]
            in {"random_order_expectation", "random"}
        ],
    )
    _write_csv(
        args.output / "primary_simple_vs_random_ci.csv",
        [
            item
            for item in public_protocol_auc_comparisons
            if item["candidate_policy_id"] == "clarifytrial_rule_v1"
            and item["baseline_policy_id"] == "random_order_expectation"
        ],
    )
    _write_csv(
        args.output / "diagnostic_three_disease_simple_vs_random_ci.csv",
        _simple_random_ci_rows(args.run_root),
    )
    _write_csv(
        args.output / "diagnostic_three_disease_planning_vs_random_ci.csv",
        _public_planning_random_ci_rows(args.run_root),
    )
    _write_csv(args.output / "exact_minus_simple_ci.csv", _ci_rows(args.run_root))
    _write_csv(args.output / "burden_ablation_three_steps.csv", _burden_rows(burden))
    _write_csv(
        args.output / "burden_ablation_paired_inference.csv",
        _burden_paired_rows(burden),
    )
    _write_csv(
        args.output / "route_choice_profile_results.csv",
        route_choice_profiles,
    )
    _write_csv(
        args.output / "route_choice_paired_differences.csv",
        route_choice_comparisons,
    )
    _write_csv(
        args.output / "shared_fact_coverage.csv",
        shared_fact_coverage,
    )
    _write_csv(
        args.output / "live_model_smoke_summary.csv",
        [
            {"routing_version": "before_code_routing", **integrated_model_smoke_before[0]},
            {"routing_version": "after_code_routing", **integrated_model_smoke_after[0]},
        ],
    )
    _write_csv(
        args.output / "model_role_routing_change.csv",
        model_role_routing_change,
    )
    _write_csv(
        args.output / "statistical_unit_audit.csv",
        _statistical_unit_audit_rows(
            common_transition=common_facts_known_transition,
            structural_summary=structural_summary_for_audit,
            burden_summary=burden,
            route_choice_summary=route_choice_summary,
            live_model_smoke=integrated_model_smoke_after[0],
        ),
    )
    _write_interpretation(
        args.output / "INTERPRETATION.md",
        policy_rows,
        overview,
        burden,
        patient_auc_comparisons,
        subgroup_rows,
        route_choice_summary,
        public_protocol_curves,
        public_protocol_auc_comparisons,
        public_protocol_efficiency,
        public_protocol_pairwise,
        disease_sensitivity_summary,
        known_age_metrics,
        known_age_comparisons,
        common_facts_known_metrics,
        common_facts_known_budget1,
        common_facts_known_auc,
        common_facts_known_transition,
        common_facts_known_categories,
        integrated_model_smoke_before,
        integrated_model_smoke_after,
        model_role_routing_change,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
