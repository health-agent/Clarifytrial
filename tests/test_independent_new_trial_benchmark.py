from __future__ import annotations

import json
from pathlib import Path

from clarifytrial.app.evaluation import run_full_workflow_evaluation
from clarifytrial.llm import DeterministicWorkflowModel


ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = ROOT / "data" / "independent_new_trial_benchmark_v1"
OLD_TRIAL_SET = ROOT / "data" / "public_protocol_benchmark_v1" / "trial_set.json"
CONFIG = ROOT / "configs" / "independent_new_trial_benchmark_v1.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_new_trial_partitions_are_disjoint_and_use_frozen_independent_gold() -> None:
    old_ids = {row["nct_id"] for row in _read(OLD_TRIAL_SET)["trials"]}
    partition_ids = {}

    for partition in ("development", "final"):
        trial_set = _read(BENCHMARK / partition / "trial_set.json")
        patients = _read(BENCHMARK / partition / "patient_pairs.json")
        gold = _read(BENCHMARK / partition / "gold_labels.json")
        ids = {row["nct_id"] for row in trial_set["trials"]}
        partition_ids[partition] = ids

        assert trial_set["status"] == "independent_new_trial_benchmark"
        assert trial_set["trial_count"] == 15
        assert patients["patient_count"] == 25
        assert gold["label_count"] == 150
        assert patients["gold_standard"][
            "independent_from_runtime_evaluator"
        ] is True
        assert patients["gold_standard"]["frozen_before_final_run"] is True
        assert all(
            row["source_location"].startswith("https://clinicaltrials.gov/study/")
            for row in trial_set["criteria"]
        )
        assert all(
            not pair["demographic_consistency"]["pregnancy_fact_withheld"]
            or pair["demographic_consistency"]["pregnancy_question_applicable"]
            for pair in patients["pairs"]
        )
        embedded = [
            {
                "patient_id": pair["patient_id"],
                "episode": episode,
                **decision,
            }
            for pair in patients["pairs"]
            for episode, key in (
                ("complete", "sufficient_evidence_episode"),
                ("initial", "insufficient_evidence_episode"),
            )
            for decision in pair[key]["expected_trial_decisions"]
        ]
        assert embedded == gold["labels"]

    assert not (partition_ids["development"] & partition_ids["final"])
    assert not (old_ids & (partition_ids["development"] | partition_ids["final"]))


def test_final_partition_runs_through_the_connected_workflow(tmp_path: Path) -> None:
    summary = run_full_workflow_evaluation(
        trial_set_path=BENCHMARK / "final" / "trial_set.json",
        patient_pairs_path=BENCHMARK / "final" / "patient_pairs.json",
        generation_config_path=CONFIG,
        destination=tmp_path,
        model=DeterministicWorkflowModel(),
        model_label="ignored-for-rules-only",
        split="heldout",
        limit=1,
        agent_architecture="rules_only",
        progress=lambda _: None,
    )

    assert summary["model"] == "deterministic-workflow"
    assert summary["agent_architecture"] == "rules_only"
    assert all(row["failed_patient_count"] == 0 for row in summary["arm_metrics"])


def test_gold_authoring_script_does_not_import_runtime_decision_code() -> None:
    source = (ROOT / "scripts" / "build_independent_new_trial_benchmark.py").read_text(
        encoding="utf-8"
    )

    assert "mechanical_checks" not in source
    assert "decision_rules" not in source
    assert "evaluate_criterion" not in source
    assert "aggregate_trial_decision" not in source
