from __future__ import annotations

import json
from pathlib import Path

from clarifytrial.reporting import build_budget_frontier


def _summary(budget: int, rate: float) -> dict:
    arms = []
    for arm, arm_rate in (
        ("fixed_order", rate / 2),
        ("immediate_coverage", rate),
        ("clarifytrial", rate),
    ):
        arms.append(
            {
                "arm": arm,
                "patient_count": 10,
                "trial_count": 50,
                "confirmed_rescue_count": round(10 * arm_rate),
                "rescue_opportunity_count": 10,
                "confirmed_rescue_rate": arm_rate,
                "false_preservation_resolved_count": round(10 * arm_rate),
                "false_preservation_count": 10,
                "false_preservation_resolution_rate": arm_rate,
                "trial_status_recovery": arm_rate,
                "mean_action_count": float(budget),
                "mean_unresolved_to_resolved": float(budget) * (1 + arm_rate),
                "new_test_count": 0,
                "additional_visit_count": 0,
            }
        )
    return {
        "model": "deterministic-workflow",
        "split": "heldout",
        "patient_count": 10,
        "action_budget": budget,
        "evaluation_scope": {},
        "broad_search_metrics": None,
        "arm_metrics": arms,
        "paired_clarifytrial_vs_fixed": {
            "baseline_arm": "fixed_order",
            "patient_count": 10,
            "mean_recovery_difference": rate / 2,
            "clarifytrial_better_patient_count": 5 if budget else 0,
            "equal_patient_count": 5 if budget else 10,
            "clarifytrial_worse_patient_count": 0,
            "two_sided_exact_sign_test_p": 0.0625 if budget else 1.0,
        },
        "paired_clarifytrial_vs_immediate_coverage": {
            "baseline_arm": "immediate_coverage",
            "patient_count": 10,
            "mean_recovery_difference": 0.0,
            "clarifytrial_better_patient_count": 0,
            "equal_patient_count": 10,
            "clarifytrial_worse_patient_count": 0,
            "two_sided_exact_sign_test_p": 1.0,
        },
    }


def test_budget_frontier_writes_tables_intervals_and_figures(tmp_path: Path) -> None:
    summaries = []
    for budget, rate in ((0, 0.0), (1, 0.5), (2, 1.0)):
        path = tmp_path / f"budget-{budget}.json"
        path.write_text(json.dumps(_summary(budget, rate)), encoding="utf-8")
        summaries.append(path)

    result = build_budget_frontier(
        workflow_summary_paths=summaries,
        output_dir=tmp_path / "frontier",
    )

    clarify = next(item for item in result["arm_summaries"] if item["arm"] == "clarifytrial")
    assert clarify["confirmed_rescue_rate_auc"] == 0.5
    assert all(item["confirmed_rescue_rate_ci95"] is not None for item in result["rows"])
    tight = result["tight_budget_comparison"]
    assert tight["action_budget"] == 1
    assert tight["baseline_trial_status_recovery"] == 0.25
    assert tight["clarifytrial_trial_status_recovery"] == 0.5
    assert tight["baseline_resolved_trials_per_action"] == 1.25
    assert tight["clarifytrial_resolved_trials_per_action"] == 1.5
    assert len(result["paired_comparisons"]) == 6
    report = (tmp_path / "frontier" / "frontier.md").read_text(encoding="utf-8")
    assert "확인 기회가 1번뿐일 때" in report
    assert "정보 한 건당 판단을 끝낸 시험" in report
    assert "0.062500" in report
    for name in (
        "frontier.json",
        "frontier.csv",
        "frontier.md",
        "candidate-rescue-by-budget.svg",
        "false-preservation-cleanup-by-budget.svg",
    ):
        assert (tmp_path / "frontier" / name).is_file()
