from __future__ import annotations

from pathlib import Path

from clarifytrial.app.evaluation import run_full_workflow_evaluation
from clarifytrial.llm import DeterministicWorkflowModel


ROOT = Path(__file__).resolve().parents[1]


def test_full_workflow_evaluation_uses_three_arms_and_batched_calls(
    tmp_path: Path,
) -> None:
    result = run_full_workflow_evaluation(
        trial_set_path=ROOT / "data/natural_evaluation_v1/preliminary_trial_set.json",
        patient_pairs_path=ROOT / "data/natural_evaluation_v2/preliminary_patient_pairs.json",
        generation_config_path=ROOT / "configs/natural_evaluation_patient_generation_v2.json",
        destination=tmp_path,
        model=DeterministicWorkflowModel(),
        model_label="deterministic-workflow",
        limit=2,
        concurrency=2,
        progress=lambda _: None,
    )

    assert result["patient_count"] == 2
    assert result["concurrency"] == 2
    assert [item["arm"] for item in result["arm_metrics"]] == [
        "no_questions",
        "fixed_order",
        "clarifytrial",
    ]
    assert all(item["failed_patient_count"] == 0 for item in result["arm_metrics"])
    no_questions, fixed, current = result["arm_metrics"]
    assert no_questions["model_call_count"] == 2
    assert fixed["model_call_count"] <= 14
    assert current["model_call_count"] <= 14
    assert (tmp_path / "cases.jsonl").exists()
    assert (tmp_path / "summary.json").exists()
