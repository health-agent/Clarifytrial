from __future__ import annotations

import json
from pathlib import Path

from clarifytrial.app.evaluation import _metrics, run_full_workflow_evaluation
from clarifytrial.llm import DeterministicWorkflowModel


ROOT = Path(__file__).resolve().parents[1]


def test_full_workflow_evaluation_uses_four_arms_and_batched_calls(
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
        include_unavailable_scenario=True,
        progress=lambda _: None,
    )

    assert result["patient_count"] == 2
    assert result["concurrency"] == 2
    assert [item["arm"] for item in result["arm_metrics"]] == [
        "no_questions",
        "fixed_order",
        "immediate_coverage",
        "clarifytrial",
    ]
    assert all(item["failed_patient_count"] == 0 for item in result["arm_metrics"])
    no_questions, fixed, immediate, current = result["arm_metrics"]
    assert no_questions["model_call_count"] == 2
    assert fixed["model_call_count"] <= 14
    assert immediate["model_call_count"] <= 14
    assert current["model_call_count"] <= 14
    assert result["decision_separation"]["retained_but_not_confirmed_count"] > 0
    assert (
        result["paired_clarifytrial_vs_immediate_coverage"]["patient_count"]
        == 2
    )
    assert len(result["unavailable_answer_metrics"]) == 4
    assert all(
        item["repeated_fact_action_count"] == 0
        for item in result["unavailable_answer_metrics"]
    )
    assert result["unavailable_answer_selection"] == (
        "each_arm_first_selected_fact"
    )

    case_rows = [
        json.loads(line)
        for line in (tmp_path / "cases.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    normal_by_patient_arm = {
        (item["patient_id"], item["arm"]): item
        for item in case_rows
        if item["scenario"] == "all_answers_available"
    }
    unavailable_rows = [
        item
        for item in case_rows
        if item["scenario"] == "first_selected_answer_unavailable"
    ]
    for item in unavailable_rows:
        normal = normal_by_patient_arm[(item["patient_id"], item["arm"])]
        expected = normal["selected_fact_ids"][:1]
        assert item["unavailable_fact_ids"] == expected

    resume_model = DeterministicWorkflowModel()
    resumed = run_full_workflow_evaluation(
        trial_set_path=ROOT / "data/natural_evaluation_v1/preliminary_trial_set.json",
        patient_pairs_path=ROOT / "data/natural_evaluation_v2/preliminary_patient_pairs.json",
        generation_config_path=ROOT / "configs/natural_evaluation_patient_generation_v2.json",
        destination=tmp_path,
        model=resume_model,
        model_label="deterministic-workflow",
        limit=2,
        concurrency=1,
        include_unavailable_scenario=True,
        resume=True,
        progress=lambda _: None,
    )
    assert resumed["resumed"] is True
    assert resume_model.call_count == {}
    assert (tmp_path / "cases.jsonl").exists()
    assert (tmp_path / "summary.json").exists()


def test_recovery_opportunities_use_gold_initial_state() -> None:
    metrics = _metrics(
        final_rows=[
            {
                "trial_id": "T1",
                "candidate_status": "retain",
                "confirmation_status": "confirmed",
            },
            {
                "trial_id": "T2",
                "candidate_status": "remove",
                "confirmation_status": "ineligible",
            },
        ],
        initial_rows=[
            {
                "trial_id": "T1",
                "candidate_status": "retain",
                "confirmation_status": "confirmed",
            },
            {
                "trial_id": "T2",
                "candidate_status": "remove",
                "confirmation_status": "ineligible",
            },
        ],
        gold_rows=[
            {
                "trial_id": "T1",
                "candidate_status": "retain",
                "confirmation_status": "confirmed",
            },
            {
                "trial_id": "T2",
                "candidate_status": "remove",
                "confirmation_status": "ineligible",
            },
        ],
        initial_gold_rows=[
            {
                "trial_id": "T1",
                "candidate_status": "retain",
                "confirmation_status": "not_confirmed",
            },
            {
                "trial_id": "T2",
                "candidate_status": "retain",
                "confirmation_status": "not_confirmed",
            },
        ],
    )

    assert metrics["rescue_opportunity_count"] == 1
    assert metrics["confirmed_rescue_count"] == 1
    assert metrics["false_preservation_count"] == 1
    assert metrics["false_preservation_resolved_count"] == 1
