from __future__ import annotations

import json

from clarifytrial.interactive import (
    build_stress_case,
    build_stress_distributions,
    run_interactive_stress,
)


def test_stress_distributions_are_joint_planning_data_not_actual_answers() -> None:
    case = build_stress_case("gated_hub", 0, seed=20260821)
    development, matched, shifted = build_stress_distributions(
        case, seed=20260821
    )

    assert len(case.trials) == 5
    assert len(case.hidden_facts) == 5
    assert all(len(item.scenarios) == 32 for item in (development, matched, shifted))
    assert all(
        abs(sum(row.probability for row in item.scenarios) - 1) < 1e-9
        for item in (development, matched, shifted)
    )
    actual_ids = {
        item.answer.evidence.evidence_id for item in case.hidden_facts
    }
    planning_ids = {
        answer.evidence.evidence_id
        for scenario in development.scenarios
        for answer in scenario.answers
    }
    assert actual_ids.isdisjoint(planning_ids)
    assert [item.probability for item in development.scenarios] != [
        item.probability for item in shifted.scenarios
    ]


def test_small_stress_run_writes_all_policy_distribution_pairs(tmp_path) -> None:
    summary_path = run_interactive_stress(
        tmp_path,
        structures_per_topology=1,
        seed=20260821,
        policy_seed=17,
        action_budget=2,
    )

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (tmp_path / "structure-results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert summary["structure_count"] == 9
    assert summary["action_budget"] == 2
    assert summary["random_policy_seed"] == 17
    assert summary["policy_count"] == 12
    assert summary["structure_state_count"] == 9 * 32
    assert summary["policy_state_evaluation_count"] == 9 * 32 * 12
    assert summary["model_calls"] == 0
    assert len(summary["policy_metrics"]) == 36
    assert len(summary["topology_metrics"]) == 9 * 3 * 12
    overlap = {
        item["topology"]: item for item in summary["topology_overlap_metrics"]
    }
    assert overlap["fully_shared"]["shared_fact_count"] == 1
    assert overlap["fully_shared"]["max_trials_per_fact"] == 5
    assert overlap["fully_separated"]["shared_fact_count"] == 0
    assert overlap["fully_separated"]["shared_fact_edge_fraction"] == 0
    assert len(summary["paired_comparisons"]) == 10
    assert len(summary["core_policy_comparisons"]) == 8
    assert len(summary["horizon_comparisons"]) == 4
    assert len(rows) == 9 * 3 * 12
    assert all(item["expected_unsafe_decisions"] == 0 for item in rows)
