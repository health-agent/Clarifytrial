"""Paired research comparison and attainable ceilings on synthetic patients.

Only the scorer enumerates the patient's actual hidden answer subsets.  These
optimistic hindsight ceilings are never inputs to an acquisition policy.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from itertools import combinations
from pathlib import Path
from statistics import mean
from time import perf_counter
from typing import Any

from .acquisition_planner import feasible_options, plan_acquisition, remaining_visits
from .burden_contracts import AcquisitionOption, AcquisitionPolicyId, AvailabilityStructure
from .burden_policy import (
    benchmark_patient_profiles, build_acquisition_catalog, explicit_limit_violations,
    select_acquisition_option,
)
from .contracts import InteractiveCase, InteractiveSnapshot
from .exact_policy import ConstrainedQuestionPlanner, compress_planning_distribution
from .oracle import decision_signature, evaluate_interactive_case, state_with_revealed_facts
from .public_benchmark import (
    build_public_case, build_public_planning_distribution, load_public_benchmark_spec,
)
from .statistics import stratified_bootstrap_mean


POLICIES = ("patient_impact", "feasible_coverage", "decision_one_step", "decision_budget")


def attainable_ceilings(
    *,
    case: InteractiveCase,
    snapshots: Mapping[frozenset[str], InteractiveSnapshot],
    catalog: Mapping[str, Sequence[AcquisitionOption]],
    profile: Any,
    action_budget: int,
) -> dict[str, float]:
    """Score best actual-answer subsets, including cumulative visit feasibility."""

    target = decision_signature(evaluate_interactive_case(case, case.full_patient_state))
    active = feasible_options(catalog, profile)
    visit_limit = remaining_visits(profile)
    score = lambda snapshot: sum(
        actual == expected for actual, expected in zip(
            decision_signature(snapshot), target, strict=True,
        )
    ) / len(target)
    full_feasible = budget_feasible = 0.0
    for subset, snapshot in snapshots.items():
        if any(not active.get(fact_id) for fact_id in subset):
            continue
        visits = sum(min(
            int(option.visit_required is True) for option in active[fact_id]
        ) for fact_id in subset)
        if visit_limit is not None and visits > visit_limit:
            continue
        value = score(snapshot)
        full_feasible = max(full_feasible, value)
        if len(subset) <= action_budget:
            budget_feasible = max(budget_feasible, value)
    return {
        "all_actions_feasible_ceiling": full_feasible,
        "budget_feasible_ceiling": budget_feasible,
        "constraint_loss": 1 - full_feasible,
        "budget_loss": full_feasible - budget_feasible,
    }


def _metrics(snapshot: InteractiveSnapshot, full: InteractiveSnapshot) -> dict[str, int | float]:
    current = {item.trial_id: item for item in snapshot.decisions}
    exact = sum(
        item == target for item, target in zip(
            decision_signature(snapshot), decision_signature(full), strict=True,
        )
    )
    confirmed = excluded = unsafe = 0
    for target in full.decisions:
        actual = current[target.trial_id]
        confirmed += actual.confirmation_status.value == target.confirmation_status.value == "confirmed"
        excluded += actual.confirmation_status.value == target.confirmation_status.value == "ineligible"
        unsafe += (
            actual.candidate_status.value == "remove" and target.candidate_status.value != "remove"
        ) or (
            actual.confirmation_status.value == "confirmed" and target.confirmation_status.value != "confirmed"
        )
    return {
        "trial_status_recovery": exact / len(full.decisions),
        "confirmed_trials": confirmed, "excluded_trials": excluded,
        "unresolved_trials": sum(
            item.confirmation_status.value not in {"confirmed", "ineligible"}
            for item in snapshot.decisions
        ),
        "unsafe_decisions": unsafe,
    }


def _run(
    *, case, catalog, profile, distribution, snapshots, policy, action_budget, planner,
) -> dict[str, Any]:
    view = case.public_policy_view().model_copy(update={"action_budget": action_budget})
    initial = case.initial_patient_state()
    revealed: frozenset[str] = frozenset()
    selected: list[AcquisitionOption] = []
    planning_states = 0
    start = perf_counter()
    for _ in range(action_budget):
        snapshot = snapshots[revealed]
        if policy.startswith("decision_"):
            planned = plan_acquisition(
                view=view, initial_state=initial, snapshot=snapshot,
                distribution=distribution, catalog=catalog, profile=profile,
                revealed_fact_ids=revealed, selected_options=selected,
                planning_horizon=1 if policy == "decision_one_step" else None,
                planner=planner,
            )
            decision = planned.decision
            planning_states += planned.planning.evaluated_states if planned.planning else 0
        else:
            decision = select_acquisition_option(
                view=view, snapshot=snapshot, revealed_fact_ids=revealed,
                catalog=catalog, profile=profile, selected_options=selected,
                policy_id=(
                    AcquisitionPolicyId.PATIENT_ADAPTIVE if policy == "patient_impact"
                    else AcquisitionPolicyId.PATIENT_LIMITS_ONLY
                ),
            )
        option = decision.selected_option
        if option is None:
            break
        selected.append(option)
        revealed = revealed | {option.fact_id}
    elapsed = perf_counter() - start
    full = evaluate_interactive_case(case, case.full_patient_state)
    result = _metrics(snapshots[revealed], full)
    result.update({
        "policy_id": policy, "actions": len(selected),
        "new_tests": sum(option.new_test_required for option in selected),
        "visits": sum(option.visit_required is True for option in selected),
        "constraint_violations": sum(
            bool(explicit_limit_violations(option, profile, selected[:position]))
            for position, option in enumerate(selected)
        ),
        "selected_fact_ids": [option.fact_id for option in selected],
        "planning_states": planning_states, "selection_seconds": elapsed,
    })
    return result


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run_acquisition_research(
    *, config_path: Path, output: Path, max_cases: int | None = None,
    budgets: Sequence[int] = (1, 2, 3),
    policies: Sequence[str] = POLICIES,
    resamples: int = 5000,
) -> dict[str, Any]:
    """Replay the existing 20 heldout synthetic patients; no new patient claim."""

    if not policies or not set(policies) <= set(POLICIES):
        raise ValueError("unknown or empty policy selection")
    if not budgets or any(budget not in {1, 2, 3} for budget in budgets):
        raise ValueError("budgets must be one, two, or three")
    if max_cases is not None and max_cases <= 0:
        raise ValueError("max_cases must be positive")
    spec = load_public_benchmark_spec(config_path)
    output.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    case_count = 0
    profiles = [
        item for item in benchmark_patient_profiles()
        if item.profile_id in {"low_extra_burden", "mobility_cost_constrained"}
    ]
    with (output / "case-results.jsonl").open("w", encoding="utf-8") as raw:
        for group in spec.groups:
            for patient in group.profiles:
                if patient.split != "heldout":
                    continue
                for mask in group.masks:
                    if max_cases is not None and case_count >= max_cases:
                        break
                    case = build_public_case(group, patient, mask)
                    view = case.public_policy_view()
                    distribution = build_public_planning_distribution(group, case, patient, mask)
                    raw_scenarios = len(distribution.scenarios)
                    distribution = compress_planning_distribution(
                        view, case.initial_patient_state(), distribution,
                    )
                    planner = ConstrainedQuestionPlanner(view, case.initial_patient_state(), distribution)
                    fact_ids = sorted(item.fact_id for item in view.available_information)
                    snapshots = {
                        frozenset(subset): evaluate_interactive_case(
                            case, state_with_revealed_facts(case, frozenset(subset)),
                        )
                        for size in range(len(fact_ids) + 1)
                        for subset in combinations(fact_ids, size)
                    }
                    for availability in AvailabilityStructure:
                        catalog = build_acquisition_catalog(view, availability)
                        for profile in profiles:
                            for budget in budgets:
                                ceilings = attainable_ceilings(
                                    case=case, snapshots=snapshots, catalog=catalog,
                                    profile=profile, action_budget=budget,
                                )
                                for policy in policies:
                                    result = _run(
                                        case=case, catalog=catalog, profile=profile,
                                        distribution=distribution, snapshots=snapshots,
                                        policy=policy, action_budget=budget,
                                        planner=planner,
                                    )
                                    row = {
                                        "case_id": case.case_id,
                                        "patient_id": f"{group.group_id}:{patient.profile_id}",
                                        "disease_group": group.group_id,
                                        "mask_id": mask.mask_id,
                                        "availability": availability.value,
                                        "burden_profile": profile.profile_id,
                                        "action_budget": budget,
                                        "raw_scenarios": raw_scenarios,
                                        "decision_scenarios": len(distribution.scenarios),
                                        **result, **ceilings,
                                        "selection_gap": ceilings["budget_feasible_ceiling"]
                                        - result["trial_status_recovery"],
                                    }
                                    if row["selection_gap"] < -1e-9:
                                        raise AssertionError("policy exceeded its hindsight feasible ceiling")
                                    raw.write(json.dumps(row, ensure_ascii=False) + "\n")
                                    raw.flush()
                                    rows.append(row)
                    case_count += 1
                    print(f"completed {case_count}: {case.case_id}", flush=True)
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for row in rows:
        key = (row["burden_profile"], row["action_budget"], row["policy_id"])
        grouped[key].append(row)
    metrics = []
    for key, selected_rows in sorted(grouped.items()):
        metrics.append({
            "burden_profile": key[0], "action_budget": key[1], "policy_id": key[2],
            "settings": len(selected_rows),
            **{name: mean(item[name] for item in selected_rows) for name in (
                "trial_status_recovery", "all_actions_feasible_ceiling",
                "budget_feasible_ceiling", "constraint_loss", "budget_loss", "selection_gap",
                "actions", "unresolved_trials", "selection_seconds",
            )},
            **{name: sum(item[name] for item in selected_rows) for name in (
                "constraint_violations", "unsafe_decisions", "new_tests", "visits",
            )},
        })
    comparisons = []
    lookup = {
        (row["case_id"], row["availability"], row["burden_profile"],
         row["action_budget"], row["policy_id"]): row for row in rows
    }
    if "decision_budget" in policies:
        for profile in profiles:
            for budget in budgets:
                for baseline in policies:
                    if baseline == "decision_budget":
                        continue
                    by_patient: dict[str, list[float]] = defaultdict(list)
                    disease_by_patient = {}
                    for row in rows:
                        if row["policy_id"] != "decision_budget" or row["burden_profile"] != profile.profile_id or row["action_budget"] != budget:
                            continue
                        paired = lookup[(row["case_id"], row["availability"], profile.profile_id, budget, baseline)]
                        by_patient[row["patient_id"]].append(row["trial_status_recovery"] - paired["trial_status_recovery"])
                        disease_by_patient[row["patient_id"]] = row["disease_group"]
                    strata: dict[str, list[float]] = defaultdict(list)
                    for patient_id, differences in by_patient.items():
                        strata[disease_by_patient[patient_id]].append(mean(differences))
                    inference = stratified_bootstrap_mean(
                        strata, cluster_unit="base_synthetic_patient", seed=20261022,
                        resamples=resamples,
                    )
                    comparisons.append({
                        "burden_profile": profile.profile_id, "action_budget": budget,
                        "candidate": "decision_budget", "baseline": baseline,
                        "patients": len(by_patient),
                        "mean_difference": inference["mean_difference"],
                        "ci_lower": inference["bootstrap_95_ci"]["lower"],
                        "ci_upper": inference["bootstrap_95_ci"]["upper"],
                        "wins": inference["wins"], "ties": inference["ties"], "losses": inference["losses"],
                    })
    summary = {
        "scope": "development diagnosis on previously evaluated synthetic patients",
        "case_count": case_count,
        "patient_count": len({row["patient_id"] for row in rows}),
        "independent_new_patients": False,
        "answer_model": "fixed development-profile kernel; no heldout hidden answers",
        "setting_count_per_budget_policy": case_count * 2 * len(profiles),
        "raw_result_count": len(rows), "metrics": metrics, "comparisons": comparisons,
        "limits": [
            "Objective structured conditions and synthetic patients only.",
            "Hindsight ceilings use true answers only in scoring, not policy input.",
            "Acquisition paths release the same idealized verified fact; actual delays and failures are not simulated.",
            "Each test requiring a visit consumes one visit unit; joint hospital visits are not modeled.",
            "Existing patient and protocol data were used in prior research; fresh patient and trial evaluation remains necessary.",
            "Selection times share a scenario cache across methods and are not fair per-policy runtime benchmarks.",
        ],
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    _write_csv(output / "policy_metrics.csv", metrics)
    if comparisons:
        _write_csv(output / "paired_comparisons.csv", comparisons)
    return summary


__all__ = ["POLICIES", "attainable_ceilings", "run_acquisition_research"]
