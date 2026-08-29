from __future__ import annotations

import csv
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


EXPECTED_FIGURES = {
    "clarifytrial-shared-information-coverage.svg",
    "clarifytrial-gray-zone-rescue.svg",
    "clarifytrial-public-budget-curves.svg",
    "clarifytrial-public-input-sensitivity.svg",
    "clarifytrial-structural-topology-budget1.svg",
    "clarifytrial-patient-limit-tradeoff.svg",
    "clarifytrial-route-choice.svg",
    "clarifytrial-compact-architecture.svg",
}


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _fixture_tables(destination: Path) -> None:
    destination.mkdir()
    _write_csv(
        destination / "budget_policy_scores.csv",
        [
            {
                "suite": "public_patient_profiles",
                "evaluation_distribution": "heldout",
                "budget": 1,
                "policy_id": "widest_impact",
                "mean_status_match_rate": 0.62,
            }
        ],
    )
    policy_rows = []
    for policy_id, values in (
        ("random_order_expectation", (0.187, 0.402, 0.612, 0.833, 0.927, 1.0)),
        ("authored_order", (0.187, 0.627, 0.853, 0.94, 0.993, 1.0)),
        ("clarifytrial_rule_v1", (0.187, 0.767, 0.88, 0.953, 0.993, 1.0)),
        ("clarifytrial_exact_coverage_v3", (0.187, 0.767, 0.88, 0.953, 0.993, 1.0)),
    ):
        policy_rows.append(
            {
                "suite": "public_patient_profiles",
                "evaluation_distribution": "heldout",
                "policy_id": policy_id,
                "budget_0_score": values[0],
                "budget_1_score": values[1],
                "budget_2_score": values[2],
                "budget_3_score": values[3],
                "budget_4_score": values[4],
                "budget_5_score": values[5],
                "mean_trial_status_recovery_normalized_auc": sum(
                    (values[index] + values[index + 1]) / 2 for index in range(5)
                ) / 5,
            }
        )
    _write_csv(destination / "budget_curve_auc.csv", policy_rows)
    _write_csv(
        destination / "public_protocol_known_age_policy_metrics.csv",
        [
            {
                "action_budget": 1,
                "policy_id": "random_order_expectation",
                "mean_trial_status_recovery": 0.8455555556,
            },
            {
                "action_budget": 1,
                "policy_id": "clarifytrial_rule_v1",
                "mean_trial_status_recovery": 0.88,
            },
        ],
    )
    _write_csv(
        destination / "public_protocol_known_age_paired_comparisons.csv",
        [
            {
                "action_budget": 1,
                "candidate_policy_id": "clarifytrial_rule_v1",
                "baseline_policy_id": "random_order_expectation",
                "metric": "trial_status_recovery",
                "mean_difference": 0.0344444444,
                "bootstrap_95_lower": 0.0111111111,
                "bootstrap_95_upper": 0.0611111111,
                "wins": 5,
                "ties": 25,
                "losses": 0,
            }
        ],
    )
    _write_csv(
        destination / "public_protocol_common_facts_known_policy_metrics.csv",
        [
            {
                "action_budget": 0,
                "policy_id": "no_questions",
                "patient_count": 30,
                "trial_count": 150,
                "rescue_opportunity_count": 22,
                "confirmed_rescue_count": 0,
                "mean_action_count": 0,
                "mean_trial_status_recovery": 0.8533333333,
            },
            {
                "action_budget": 1,
                "policy_id": "random_order_expectation",
                "patient_count": 30,
                "trial_count": 150,
                "rescue_opportunity_count": 22,
                "confirmed_rescue_count": 13.3333333333,
                "mean_action_count": 0.4666666667,
                "mean_trial_status_recovery": 0.9422222222,
            },
            {
                "action_budget": 1,
                "policy_id": "clarifytrial_rule_v1",
                "patient_count": 30,
                "trial_count": 150,
                "rescue_opportunity_count": 22,
                "confirmed_rescue_count": 14,
                "mean_action_count": 0.4666666667,
                "mean_trial_status_recovery": 0.9466666667,
            },
        ],
    )
    _write_csv(
        destination / "public_protocol_common_facts_known_budget1.csv",
        [
            {
                "action_budget": 1,
                "candidate_policy_id": "clarifytrial_rule_v1",
                "baseline_policy_id": "random_order_expectation",
                "metric": "trial_status_recovery",
                "mean_difference": 0.0044444444,
                "bootstrap_95_lower": 0.0,
                "bootstrap_95_upper": 0.0133333333,
                "wins": 1,
                "ties": 29,
                "losses": 0,
            }
        ],
    )

    topology_rows = []
    differences = {
        "fully_shared": 0.22,
        "shared_hub": 0.12,
        "chain": 0.04,
        "gated_hub": 0.08,
        "low_overlap": 0.02,
        "fully_separated": 0.0,
        "overlapping_pairs": 0.06,
        "three_way": 0.10,
        "cost_conflict": 0.03,
    }
    for topology, difference in differences.items():
        topology_rows.append(
            {
                "suite": "synthetic_graph_stress",
                "subgroup_type": "graph_topology",
                "subgroup": topology,
                "evaluation_distribution": "similar_heldout",
                "budget": 1,
                "candidate_policy_id": "clarifytrial_rule_v1",
                "baseline_policy_id": "random",
                "difference": difference,
            }
        )
    _write_csv(destination / "simple_vs_random_subgroups.csv", topology_rows)

    _write_csv(
        destination / "burden_ablation_three_steps.csv",
        [
            {
                "stage": "1_exact_fixed_route",
                "feasible_information_status_match_rate": 0.795,
                "new_test_total": 21,
                "additional_visit_total": 56,
                "mean_pending_trial_count": 0.875,
                "fully_resolved_setting_count": 42,
                "setting_pair_count": 80,
                "mean_summed_route_delay_hours": 58.30625,
            },
            {
                "stage": "2_apply_explicit_patient_limits",
                "feasible_information_status_match_rate": 0.9125,
                "new_test_total": 0,
                "additional_visit_total": 0,
                "mean_pending_trial_count": 1.2375,
                "fully_resolved_setting_count": 32,
                "setting_pair_count": 80,
                "mean_summed_route_delay_hours": 67.6125,
            },
            {
                "stage": "3_rank_remaining_paths_by_patient_preferences",
                "feasible_information_status_match_rate": 0.9125,
                "new_test_total": 0,
                "additional_visit_total": 0,
                "mean_pending_trial_count": 1.2375,
                "fully_resolved_setting_count": 32,
                "setting_pair_count": 80,
                "mean_summed_route_delay_hours": 67.6125,
            },
        ],
    )
    _write_csv(
        destination / "route_choice_profile_results.csv",
        [
            {
                "patient_profile_id": "low_extra_burden",
                "mean_summed_route_delay_hours": 160.2,
                "existing_official_result_count": 89,
                "new_noninvasive_test_count": 0,
                "same_final_judgment_masked_case_count": 40,
            },
            {
                "patient_profile_id": "mobility_cost_constrained",
                "mean_summed_route_delay_hours": 160.2,
                "existing_official_result_count": 89,
                "new_noninvasive_test_count": 0,
                "same_final_judgment_masked_case_count": 40,
            },
            {
                "patient_profile_id": "time_urgent",
                "mean_summed_route_delay_hours": 17.8,
                "existing_official_result_count": 0,
                "new_noninvasive_test_count": 89,
                "same_final_judgment_masked_case_count": 40,
            },
        ],
    )
    _write_csv(
        destination / "shared_fact_coverage.csv",
        [
            {
                "criterion_count": 202,
                "criteria_whose_fact_is_used_by_at_least_2_trials": 130,
                "share_of_criteria_with_a_cross_trial_fact": 130 / 202,
                "age_years_shared_criterion_count": 72,
                "pregnancy_or_lactation_shared_criterion_count": 36,
                "active_serious_infection_shared_criterion_count": 18,
                "other_shared_criterion_count": 4,
            }
        ],
    )


def test_presentation_figures_are_rendered_from_exported_tables(tmp_path: Path) -> None:
    input_dir = tmp_path / "evidence"
    output_dir = tmp_path / "figures"
    _fixture_tables(input_dir)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/render_presentation_evidence_figures.py",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert {path.name for path in output_dir.iterdir()} == EXPECTED_FIGURES
    for filename in EXPECTED_FIGURES:
        path = output_dir / filename
        root = ET.parse(path).getroot()
        assert root.tag.endswith("svg")
        assert root.attrib["width"] == "1200"
        assert root.attrib["height"] == "675"

    coverage = (output_dir / "clarifytrial-shared-information-coverage.svg").read_text(
        encoding="utf-8"
    )
    burden = (output_dir / "clarifytrial-patient-limit-tradeoff.svg").read_text(
        encoding="utf-8"
    )
    topology = (
        output_dir / "clarifytrial-structural-topology-budget1.svg"
    ).read_text(encoding="utf-8")
    sensitivity = (
        output_dir / "clarifytrial-public-input-sensitivity.svg"
    ).read_text(encoding="utf-8")
    rescue = (output_dir / "clarifytrial-gray-zone-rescue.svg").read_text(
        encoding="utf-8"
    )
    assert "한 번의 확인으로 정리됐다" in rescue
    assert "후보 14건이 확정됐다" not in rescue
    route_choice = (output_dir / "clarifytrial-route-choice.svg").read_text(
        encoding="utf-8"
    )
    assert "64.4%" in coverage
    assert "130개" in coverage
    assert "91.25%" not in burden
    assert "58.3시간" not in burden
    assert "153.0시간" not in route_choice
    assert "17.0시간" not in route_choice
    assert "1.24개" in burden
    assert "32/80" in burden
    assert "시험마다 필요한 정보가 완전히 다름" in topology
    assert "+0.0%p" in topology
    assert "+0.44%p" in sensitivity
    assert "개선 1명 · 동일 29명 · 악화 0명" in sensitivity
    assert "22건" in rescue
    assert "14건" in rescue
    assert "8건" in rescue
    assert "실제로 확인한 횟수 14회" in rescue


def test_missing_tables_are_reported_before_any_figure_is_written(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "empty-evidence"
    output_dir = tmp_path / "figures"
    input_dir.mkdir()

    result = subprocess.run(
        [
            sys.executable,
            "scripts/render_presentation_evidence_figures.py",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "다음 결과 표가 없습니다" in result.stderr
    assert "budget_policy_scores.csv" in result.stderr
    assert "shared_fact_coverage.csv" in result.stderr
    assert not output_dir.exists()


def test_invalid_table_leaves_no_partial_figure_set(tmp_path: Path) -> None:
    input_dir = tmp_path / "evidence"
    output_dir = tmp_path / "figures"
    _fixture_tables(input_dir)
    (input_dir / "simple_vs_random_subgroups.csv").write_text(
        "suite,budget\nsynthetic_graph_stress,1\n", encoding="utf-8"
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/render_presentation_evidence_figures.py",
            "--input-dir",
            str(input_dir),
            "--output-dir",
            str(output_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "구조별 결과를 찾지 못했습니다" in result.stderr
    assert not output_dir.exists()
