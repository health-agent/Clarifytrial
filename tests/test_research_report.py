from __future__ import annotations

import json
from pathlib import Path

from clarifytrial.reporting import build_research_report


def _question_document(current: float) -> dict:
    common = {
        "action_budget": 3,
        "split": "heldout",
        "input_state": "fully_missing",
        "patient_count": 30,
        "mean_action_count": 3.0,
        "mean_needed_fact_recall": 0.6,
        "mean_unnecessary_action_count": 1.0,
    }
    return {
        "summaries": [
            {**common, "policy_id": "fixed_source_order", "trial_status_recovery": 0.75},
            {
                **common,
                "policy_id": "clarifytrial_exact_coverage_v3",
                "trial_status_recovery": current,
                "mean_needed_fact_recall": 1.0,
                "mean_unnecessary_action_count": 0.3,
            },
        ]
    }


def test_report_figures_read_values_from_evaluation_json(tmp_path: Path) -> None:
    question_path = tmp_path / "question.json"
    question_path.write_text(json.dumps(_question_document(0.89)), encoding="utf-8")
    output = tmp_path / "report"
    build_research_report(destination=output, question_policy_path=question_path)
    first_svg = (output / "question-policy.svg").read_text(encoding="utf-8")
    assert "89.0%" in first_svg
    assert "75.0%" in first_svg

    question_path.write_text(json.dumps(_question_document(0.91)), encoding="utf-8")
    build_research_report(destination=output, question_policy_path=question_path)
    second_svg = (output / "question-policy.svg").read_text(encoding="utf-8")
    assert "91.0%" in second_svg
    assert "89.0%" not in second_svg
    assert (output / "metrics.csv").exists()
    assert (output / "report.md").exists()
