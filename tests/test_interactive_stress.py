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
    )

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    rows = [
        json.loads(line)
        for line in (tmp_path / "structure-results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ]

    assert summary["structure_count"] == 7
    assert summary["model_calls"] == 0
    assert len(summary["policy_metrics"]) == 30
    assert len(summary["topology_metrics"]) == 7 * 3 * 10
    assert len(summary["paired_comparisons"]) == 8
    assert len(summary["horizon_comparisons"]) == 4
    assert len(rows) == 7 * 3 * 10
    assert all(item["expected_unsafe_decisions"] == 0 for item in rows)
