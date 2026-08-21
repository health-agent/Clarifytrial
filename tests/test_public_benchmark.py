from __future__ import annotations

import json
from pathlib import Path

from clarifytrial.interactive import (
    audit_public_sources,
    build_public_case,
    build_public_planning_distribution,
    load_public_benchmark_spec,
    run_public_grid_stress,
)


CONFIG_PATH = Path("configs/interactive_public_benchmark_v1.json")


def _fake_source_cache(spec, destination: Path) -> Path:
    records = destination / "records"
    records.mkdir(parents=True)
    for group in spec.groups:
        for trial in group.trials:
            record = {
                "protocolSection": {
                    "identificationModule": {"nctId": trial.nct_id},
                    "eligibilityModule": {
                        "eligibilityCriteria": "\n".join(
                            criterion.source_statement
                            for criterion in trial.criteria
                        )
                    },
                }
            }
            (records / f"{trial.nct_id}.json").write_text(
                json.dumps(record), encoding="utf-8"
            )
    return destination


def test_public_config_has_declared_patient_trial_and_criterion_counts() -> None:
    spec = load_public_benchmark_spec(CONFIG_PATH)

    profiles = [profile for group in spec.groups for profile in group.profiles]
    criteria = [
        criterion
        for group in spec.groups
        for trial in group.trials
        for criterion in trial.criteria
    ]

    assert len(spec.groups) == 3
    assert len(profiles) == 30
    assert sum(item.split == "development" for item in profiles) == 10
    assert sum(item.split == "heldout" for item in profiles) == 20
    assert sum(len(group.trials) for group in spec.groups) == 15
    assert len(criteria) == 80
    assert all(len(group.facts) == 7 for group in spec.groups)
    assert all(len(mask.visible_facts) == 2 for group in spec.groups for mask in group.masks)


def test_public_source_audit_checks_all_structured_criteria(tmp_path) -> None:
    spec = load_public_benchmark_spec(CONFIG_PATH)
    cache = _fake_source_cache(spec, tmp_path / "source-cache")

    audit = audit_public_sources(spec, cache)

    assert len(audit) == 80
    assert all(item["source_token_coverage"] == 1 for item in audit)


def test_planning_weights_do_not_change_with_hidden_patient_answers() -> None:
    spec = load_public_benchmark_spec(CONFIG_PATH)
    group = spec.groups[0]
    profile = group.profiles[0]
    mask = group.masks[0]
    hidden_codes = [
        fact.code for fact in group.facts if fact.code not in mask.visible_facts
    ]
    changed_values = dict(profile.values)
    for code in hidden_codes:
        fact = next(item for item in group.facts if item.code == code)
        changed_values[code] = next(
            value for value in fact.values if value != profile.values[code]
        )
    changed = profile.model_copy(update={"values": changed_values})

    original_case = build_public_case(group, profile, mask)
    changed_case = build_public_case(group, changed, mask)
    original = build_public_planning_distribution(
        group, original_case, profile, mask
    )
    alternative = build_public_planning_distribution(
        group, changed_case, changed, mask
    )

    assert [item.probability for item in original.scenarios] == [
        item.probability for item in alternative.scenarios
    ]
    authored_answer_ids = {
        item.answer.evidence.evidence_id for item in original_case.hidden_facts
    }
    planning_ids = {
        answer.evidence.evidence_id
        for scenario in original.scenarios
        for answer in scenario.answers
    }
    assert authored_answer_ids.isdisjoint(planning_ids)


def test_small_public_grid_run_writes_frozen_baseline_comparison(tmp_path) -> None:
    original = load_public_benchmark_spec(CONFIG_PATH).model_dump(mode="json")
    group = original["groups"][0]
    allowed = {
        fact["code"]: fact["values"][:2] for fact in group["facts"]
    }
    for fact in group["facts"]:
        fact["values"] = allowed[fact["code"]]
    group["profiles"] = [
        {
            "profile_id": "small-development",
            "split": "development",
            "values": {code: values[0] for code, values in allowed.items()},
        },
        {
            "profile_id": "small-heldout",
            "split": "heldout",
            "values": {code: values[1] for code, values in allowed.items()},
        },
    ]
    original["groups"] = [group]
    config_path = tmp_path / "small-config.json"
    config_path.write_text(json.dumps(original), encoding="utf-8")
    small_spec = load_public_benchmark_spec(config_path)
    cache = _fake_source_cache(small_spec, tmp_path / "source-cache")

    summary_path = run_public_grid_stress(
        config_path,
        cache,
        tmp_path / "run",
        action_budget=3,
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary["visible_context_count"] == 8
    assert summary["scenario_policy_evaluations"] == 8 * 32 * 7
    assert summary["policy_count"] == 7
    assert len(summary["policy_metrics"]) == 3 * 7
    assert summary["comparison"]["baseline_selected_on_development"] in {
        "widest_impact",
        "impact_per_cost",
        "clarifytrial_rule_v1",
        "outcome_entropy",
    }
    assert len(summary["comparison"]["comparisons"]) == 4
