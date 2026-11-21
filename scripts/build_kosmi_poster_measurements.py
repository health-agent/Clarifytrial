from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from clarifytrial.interactive.statistics import stratified_bootstrap_mean


SELECTED_POLICIES = (
    "no_questions",
    "random_order_expectation",
    "clarifytrial_rule_v1",
    "clarifytrial_exact_coverage_v3",
)
POLICY_COMPARISONS = (
    ("clarifytrial_rule_v1", "random_order_expectation"),
    ("clarifytrial_exact_coverage_v3", "clarifytrial_rule_v1"),
)
PROGRESS_POLICIES = (
    "clarifytrial_rule_v1",
    "clarifytrial_exact_coverage_v3",
    "exact_decision_tree_expected_horizon_1_v1",
)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _percentile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires at least one value")
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _patient_rows(
    rows: list[dict[str, Any]],
    policy_id: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["split"] == "heldout" and row["policy_id"] == policy_id:
            grouped[(row["group_id"], row["profile_id"])].append(row)
    result = {}
    for key, items in grouped.items():
        result[key] = {
            "group_id": key[0],
            "profile_id": key[1],
            "mask_count": len(items),
            "mean_trial_recovery": mean(item["trial_recovery"] for item in items),
            "mean_actions": mean(item["actions"] for item in items),
            "mean_route_cost": mean(item["route_cost"] for item in items),
            "rescue_opportunities": sum(
                item["rescue_opportunity_count"] for item in items
            ),
            "confirmed_rescues": sum(
                item["confirmed_rescue_count"] for item in items
            ),
            "cleanup_opportunities": sum(
                item["cleanup_opportunity_count"] for item in items
            ),
            "ineligible_cleanups": sum(
                item["ineligible_cleanup_count"] for item in items
            ),
            "unsafe_decisions": sum(item["unsafe_decisions"] for item in items),
        }
    return result


def _stratified_ratio_difference(
    candidate: dict[tuple[str, str], dict[str, Any]],
    baseline: dict[tuple[str, str], dict[str, Any]],
    *,
    numerator: str,
    denominator: str,
    seed: int = 20_260_902,
    resamples: int = 5_000,
) -> dict[str, Any]:
    if candidate.keys() != baseline.keys():
        raise ValueError("candidate and baseline patient keys do not match")
    by_group: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = defaultdict(
        list
    )
    for key in sorted(candidate):
        candidate_row = candidate[key]
        baseline_row = baseline[key]
        if candidate_row[denominator] != baseline_row[denominator]:
            raise ValueError(f"opportunity denominator differs for patient {key}")
        by_group[key[0]].append((candidate_row, baseline_row))

    def rates(
        pairs: list[tuple[dict[str, Any], dict[str, Any]]],
    ) -> tuple[float, float]:
        candidate_denominator = sum(item[0][denominator] for item in pairs)
        baseline_denominator = sum(item[1][denominator] for item in pairs)
        if candidate_denominator == 0 or baseline_denominator == 0:
            raise ValueError(f"zero opportunity denominator for {denominator}")
        candidate_rate = (
            sum(item[0][numerator] for item in pairs) / candidate_denominator
        )
        baseline_rate = sum(item[1][numerator] for item in pairs) / baseline_denominator
        return candidate_rate, baseline_rate

    all_pairs = [pair for group in by_group.values() for pair in group]
    candidate_rate, baseline_rate = rates(all_pairs)
    generator = random.Random(seed)
    samples = []
    attempts = 0
    while len(samples) < resamples:
        attempts += 1
        if attempts > resamples * 20:
            raise ValueError(
                f"could not draw {resamples} valid bootstrap samples for {denominator}"
            )
        drawn = [
            generator.choice(group)
            for group in by_group.values()
            for _ in group
        ]
        try:
            drawn_candidate, drawn_baseline = rates(drawn)
        except ValueError:
            continue
        samples.append(drawn_candidate - drawn_baseline)
    return {
        "cluster_unit": "base_patient",
        "strata": ";".join(sorted(by_group)),
        "base_patient_count": len(all_pairs),
        "bootstrap_seed": seed,
        "bootstrap_resamples": resamples,
        "bootstrap_draw_attempts": attempts,
        "candidate_value": candidate_rate,
        "baseline_value": baseline_rate,
        "difference": candidate_rate - baseline_rate,
        "bootstrap_95_lower": _percentile(samples, 0.025),
        "bootstrap_95_upper": _percentile(samples, 0.975),
    }


def _stratified_mean_difference(
    candidate: dict[tuple[str, str], dict[str, Any]],
    baseline: dict[tuple[str, str], dict[str, Any]],
    metric: str,
) -> dict[str, Any]:
    if candidate.keys() != baseline.keys():
        raise ValueError("candidate and baseline patient keys do not match")
    differences: dict[str, list[float]] = defaultdict(list)
    candidate_values = []
    baseline_values = []
    for key in sorted(candidate):
        candidate_value = float(candidate[key][metric])
        baseline_value = float(baseline[key][metric])
        differences[key[0]].append(candidate_value - baseline_value)
        candidate_values.append(candidate_value)
        baseline_values.append(baseline_value)
    inference = stratified_bootstrap_mean(
        differences,
        cluster_unit="base_patient",
    )
    return {
        "candidate_value": mean(candidate_values),
        "baseline_value": mean(baseline_values),
        "difference": inference["mean_difference"],
        "bootstrap_95_lower": inference["bootstrap_95_ci"]["lower"],
        "bootstrap_95_upper": inference["bootstrap_95_ci"]["upper"],
        "base_patient_count": inference["pair_count"],
        "cluster_unit": inference["cluster_unit"],
        "strata": ";".join(inference["strata"]),
        "bootstrap_seed": inference["bootstrap_seed"],
        "bootstrap_resamples": inference["bootstrap_resamples"],
        "wins": inference["wins"],
        "ties": inference["ties"],
        "losses": inference["losses"],
        "two_sided_exact_sign_test_p": inference[
            "two_sided_exact_sign_test_p"
        ],
    }


def _measurement_rows(
    budget: int,
    summary: dict[str, Any],
) -> list[dict[str, Any]]:
    result = []
    no_question = next(
        item
        for item in summary["policy_metrics"]
        if item["split"] == "heldout" and item["policy_id"] == "no_questions"
    )
    for policy_id in SELECTED_POLICIES:
        if policy_id == "no_questions" and budget != 1:
            continue
        metric = next(
            item
            for item in summary["policy_metrics"]
            if item["split"] == "heldout" and item["policy_id"] == policy_id
        )
        run_count = int(metric["run_count"])
        trial_occurrences = run_count * int(summary["candidate_trials_per_case"])
        initially_resolved = round(
            float(no_question["mean_trial_recovery"]) * trial_occurrences
        )
        result.append(
            {
                "budget": 0 if policy_id == "no_questions" else budget,
                "policy_id": policy_id,
                "base_patient_count": summary["heldout_patient_count"],
                "masks_per_patient": summary["masks_per_patient"],
                "masked_case_count": run_count,
                "patient_trial_occurrence_count": trial_occurrences,
                "initially_resolved_trial_occurrence_count": initially_resolved,
                "initially_unresolved_trial_occurrence_count": (
                    trial_occurrences - initially_resolved
                ),
                "rescue_opportunity_count": metric["rescue_opportunity_count"],
                "confirmed_rescue_count": metric["confirmed_rescue_count"],
                "confirmed_rescue_rate": metric["confirmed_rescue_rate"],
                "cleanup_opportunity_count": metric["cleanup_opportunity_count"],
                "ineligible_cleanup_count": metric["ineligible_cleanup_count"],
                "ineligible_cleanup_rate": metric["ineligible_cleanup_rate"],
                "final_state_agreement": metric["mean_trial_recovery"],
                "final_state_match_count": (
                    metric["mean_trial_recovery"] * trial_occurrences
                ),
                "mean_questions_per_masked_case": metric["mean_actions"],
                "mean_route_cost_per_masked_case": metric["mean_route_cost"],
                "unsafe_decision_count": metric["total_unsafe_decisions"],
            }
        )
    return result


def _budget_auc_patient_rows(
    patients_by_budget: dict[
        int,
        dict[str, dict[tuple[str, str], dict[str, Any]]],
    ],
    policy_id: str,
) -> dict[tuple[str, str], dict[str, Any]]:
    no_question = patients_by_budget[1]["no_questions"]
    result = {}
    for key in sorted(no_question):
        scores = [no_question[key]["mean_trial_recovery"]]
        scores.extend(
            patients_by_budget[budget][policy_id][key]["mean_trial_recovery"]
            for budget in (1, 2, 3)
        )
        raw_auc = sum(
            (scores[index] + scores[index + 1]) / 2 for index in range(3)
        )
        result[key] = {
            "group_id": key[0],
            "profile_id": key[1],
            "normalized_budget_auc": raw_auc / 3,
        }
    return result


def build(transition_root: Path, output_dir: Path) -> dict[str, Any]:
    measurement_rows = []
    comparison_rows = []
    source_runs = []
    patients_by_budget = {}
    summaries_by_budget = {}
    for budget in (1, 2, 3):
        base = transition_root / f"budget-{budget}"
        summary_path = base / "summary.json"
        case_path = base / "case-results.jsonl"
        summary = _read_json(summary_path)
        summaries_by_budget[budget] = summary
        rows = _read_jsonl(case_path)
        if int(summary["action_budget"]) != budget:
            raise ValueError(f"budget mismatch in {summary_path}")
        patient_by_policy = {
            policy_id: _patient_rows(rows, policy_id)
            for policy_id in SELECTED_POLICIES
        }
        patients_by_budget[budget] = patient_by_policy
        if any(len(items) != 20 for items in patient_by_policy.values()):
            raise ValueError("expected 20 heldout base patients for every policy")
        if any(
            item["mask_count"] != 2
            for items in patient_by_policy.values()
            for item in items.values()
        ):
            raise ValueError("expected two repeated masks for every heldout patient")
        measurement_rows.extend(_measurement_rows(budget, summary))
        for candidate_id, baseline_id in POLICY_COMPARISONS:
            candidate = patient_by_policy[candidate_id]
            baseline = patient_by_policy[baseline_id]
            for metric_name, source_metric in (
                ("final_state_agreement", "mean_trial_recovery"),
                ("mean_questions_per_masked_case", "mean_actions"),
            ):
                comparison_rows.append(
                    {
                        "budget": budget,
                        "candidate_policy_id": candidate_id,
                        "baseline_policy_id": baseline_id,
                        "metric": metric_name,
                        **_stratified_mean_difference(
                            candidate,
                            baseline,
                            source_metric,
                        ),
                    }
                )
            for metric_name, numerator, denominator in (
                (
                    "confirmed_rescue_rate",
                    "confirmed_rescues",
                    "rescue_opportunities",
                ),
                (
                    "ineligible_cleanup_rate",
                    "ineligible_cleanups",
                    "cleanup_opportunities",
                ),
            ):
                comparison_rows.append(
                    {
                        "budget": budget,
                        "candidate_policy_id": candidate_id,
                        "baseline_policy_id": baseline_id,
                        "metric": metric_name,
                        **_stratified_ratio_difference(
                            candidate,
                            baseline,
                            numerator=numerator,
                            denominator=denominator,
                        ),
                    }
                )
        source_runs.append(
            {
                "budget": budget,
                "summary": str(summary_path),
                "case_results": str(case_path),
                "runtime_seconds": summary["runtime_seconds"],
            }
        )

    budget_auc_comparisons = []
    for candidate_id, baseline_id in POLICY_COMPARISONS:
        candidate = _budget_auc_patient_rows(patients_by_budget, candidate_id)
        baseline = _budget_auc_patient_rows(patients_by_budget, baseline_id)
        budget_auc_comparisons.append(
            {
                "candidate_policy_id": candidate_id,
                "baseline_policy_id": baseline_id,
                "metric": "normalized_trapezoid_auc_over_budgets_0_to_3",
                **_stratified_mean_difference(
                    candidate,
                    baseline,
                    "normalized_budget_auc",
                ),
            }
        )

    no_question = next(
        item
        for item in measurement_rows
        if item["budget"] == 0 and item["policy_id"] == "no_questions"
    )
    if (
        no_question["rescue_opportunity_count"] != 30
        or no_question["cleanup_opportunity_count"] != 90
        or no_question["initially_resolved_trial_occurrence_count"] != 80
    ):
        raise ValueError("unexpected transition opportunity composition")
    if any(item["unsafe_decision_count"] != 0 for item in measurement_rows):
        raise ValueError("selected policy set contains an unsafe decision")
    for item in measurement_rows:
        if (
            item["rescue_opportunity_count"] != 30
            or item["cleanup_opportunity_count"] != 90
        ):
            raise ValueError("transition opportunity counts changed across policies")
        resolved_match_count = (
            item["initially_resolved_trial_occurrence_count"]
            + item["confirmed_rescue_count"]
            + item["ineligible_cleanup_count"]
        )
        if abs(item["final_state_match_count"] - resolved_match_count) > 1e-9:
            raise ValueError("directional counts do not reconstruct final agreement")
    if any(summary["model_calls"] != 0 for summary in summaries_by_budget.values()):
        raise ValueError("poster transition benchmark unexpectedly called a model")

    first_summary = summaries_by_budget[1]
    config = _read_json(Path(first_summary["config_path"]))
    public_trial_count = sum(len(group["trials"]) for group in config["groups"])
    structured_criterion_count = sum(
        len(trial["criteria"])
        for group in config["groups"]
        for trial in group["trials"]
    )
    if structured_criterion_count != first_summary["source_audit_criterion_count"]:
        raise ValueError("source audit does not cover every structured criterion")

    _write_csv(output_dir / "transition_budget_metrics.csv", measurement_rows)
    _write_csv(output_dir / "transition_policy_comparisons.csv", comparison_rows)
    _write_csv(
        output_dir / "transition_budget_auc_comparisons.csv",
        budget_auc_comparisons,
    )
    payload = {
        "evaluation": "bidirectional confirmed/ineligible transition measurement",
        "scope": {
            "public_trial_count": public_trial_count,
            "disease_group_count": len(config["groups"]),
            "structured_criterion_count": structured_criterion_count,
            "development_base_patient_count": first_summary[
                "development_patient_count"
            ],
            "heldout_base_patient_count": first_summary["heldout_patient_count"],
            "masks_per_patient": first_summary["masks_per_patient"],
            "heldout_masked_case_count": first_summary["heldout_patient_count"]
            * first_summary["masks_per_patient"],
            "candidate_trials_per_case": first_summary[
                "candidate_trials_per_case"
            ],
            "heldout_patient_trial_occurrence_count": no_question[
                "patient_trial_occurrence_count"
            ],
            "initially_resolved_trial_occurrence_count": no_question[
                "initially_resolved_trial_occurrence_count"
            ],
            "initially_unresolved_trial_occurrence_count": no_question[
                "initially_unresolved_trial_occurrence_count"
            ],
            "confirmed_rescue_opportunity_count": int(
                no_question["rescue_opportunity_count"]
            ),
            "ineligible_cleanup_opportunity_count": int(
                no_question["cleanup_opportunity_count"]
            ),
            "random_order_count_per_masked_case": first_summary[
                "random_order_count_per_masked_case"
            ],
            "model_calls": first_summary["model_calls"],
            "action_budgets": [0, 1, 2, 3],
        },
        "independent_unit": (
            "20 heldout base patients; two masks are repeated measurements"
        ),
        "policy_measurements": measurement_rows,
        "paired_policy_comparisons": comparison_rows,
        "paired_budget_auc_comparisons": budget_auc_comparisons,
        "source_runs": source_runs,
        "limitations": [
            (
                "All patients are synthetic and all trial criteria are selected "
                "public criteria."
            ),
            (
                "Patient-trial occurrences and two masks per patient are not "
                "independent samples."
            ),
            (
                "Transition rates describe recovery of the synthetic "
                "full-information state, not clinical eligibility accuracy."
            ),
        ],
    }
    _write_json(output_dir / "summary.json", payload)
    return payload


def _candidate_trajectory(case, fact_ids: list[str]) -> list[dict[str, Any]]:
    """Replay one saved sequence; the full state is used only for scoring."""
    from clarifytrial.contracts import CandidateStatus, ConfirmationStatus
    from clarifytrial.interactive.oracle import evaluate_policy_view

    view = case.public_policy_view()
    answers = {item.request.fact_id: item.answer.evidence for item in case.hidden_facts}
    if len(fact_ids) > case.action_budget or len(set(fact_ids)) != len(fact_ids):
        raise ValueError("saved sequence exceeds budget or repeats a fact")
    if not set(fact_ids) <= answers.keys():
        raise ValueError("saved sequence contains an unknown fact")
    state = case.initial_patient_state()
    initial = {item.trial_id: item for item in evaluate_policy_view(view, state).decisions}
    full_state = state.model_copy(update={
        "facts": [*state.facts, *(answers[key] for key in sorted(answers))],
    })
    full = {item.trial_id: item for item in evaluate_policy_view(view, full_state).decisions}
    eligible = {key for key, item in full.items()
                if item.confirmation_status is ConfirmationStatus.CONFIRMED}
    pending = {key for key, item in initial.items()
               if item.candidate_status is CandidateStatus.RETAIN
               and item.confirmation_status is ConfirmationStatus.NOT_CONFIRMED}
    trajectory = []
    for step in range(case.action_budget + 1):
        if 0 < step <= len(fact_ids):
            state = state.model_copy(update={"facts": [*state.facts, answers[fact_ids[step - 1]]]})
        current = {item.trial_id: item for item in evaluate_policy_view(view, state).decisions}
        matches = {key for key in full if (
            current[key].candidate_status, current[key].confirmation_status
        ) == (full[key].candidate_status, full[key].confirmation_status)}
        confirmed = eligible & matches
        trajectory.append({
            "step": step,
            "actions_used": min(step, len(fact_ids)),
            "reference_eligible_count": len(eligible),
            "reference_confirmed_ids": sorted(confirmed),
            "newly_confirmed_ids": sorted(confirmed & pending),
            "ineligible_cleanup_count": sum(
                key in matches and full[key].confirmation_status is ConfirmationStatus.INELIGIBLE
                for key in pending
            ),
            "state_match_count": len(matches),
        })
    return trajectory


def build_supplement(transition_root: Path, output_dir: Path, config_path: Path) -> dict[str, Any]:
    """Add patient-level interpretation without rerunning or changing policies."""
    from clarifytrial.interactive.public_benchmark import build_public_case, load_public_benchmark_spec

    spec = load_public_benchmark_spec(config_path)
    cases = {
        (group.group_id, profile.profile_id, mask.mask_id): build_public_case(group, profile, mask)
        for group in spec.groups for profile in group.profiles if profile.split == "heldout"
        for mask in group.masks
    }
    saved_rows = _read_jsonl(transition_root / "budget-3" / "case-results.jsonl")
    progress_rows = []
    seen = set()
    for row in saved_rows:
        if row["split"] != "heldout" or row["policy_id"] not in PROGRESS_POLICIES:
            continue
        key = (row["group_id"], row["profile_id"], row["mask_id"])
        entry_key = (*key, row["policy_id"])
        if entry_key in seen or row["action_budget"] != 3:
            raise ValueError("duplicate case-policy or unexpected saved budget")
        seen.add(entry_key)
        case = cases[key]
        selected = row["selected_fact_ids"]
        if row["actions"] != len(selected):
            raise ValueError("saved action count does not match its sequence")
        path = _candidate_trajectory(case, selected)
        final = path[-1]
        if (len(final["newly_confirmed_ids"]) != row["confirmed_rescue_count"]
                or final["ineligible_cleanup_count"] != row["ineligible_cleanup_count"]
                or abs(final["state_match_count"] / len(case.trials) - row["trial_recovery"]) > 1e-9):
            raise ValueError(f"replay differs from original result: {entry_key}")
        result = {"group_id": key[0], "profile_id": key[1], "mask_id": key[2],
                  "policy_id": row["policy_id"], "action_budget": 3,
                  "reference_eligible_count": final["reference_eligible_count"],
                  "initial_confirmed_count": len(path[0]["reference_confirmed_ids"]),
                  "newly_confirmed_count": len(final["newly_confirmed_ids"]),
                  "ineligible_cleanup_count": final["ineligible_cleanup_count"],
                  "state_match_count": final["state_match_count"],
                  "first_confirmation_step": next((p["step"] for p in path if p["reference_confirmed_ids"]), None),
                  "first_new_confirmation_step": next((p["step"] for p in path if p["newly_confirmed_ids"]), None),
                  "selected_fact_ids": json.dumps(selected),
                  "newly_confirmed_trial_ids": json.dumps(final["newly_confirmed_ids"])}
        result.update({f"confirmed_count_step_{p['step']}": len(p["reference_confirmed_ids"]) for p in path})
        progress_rows.append(result)
    expected = {(*key, policy_id) for key in cases for policy_id in PROGRESS_POLICIES}
    if seen != expected:
        raise ValueError("saved results do not cover every heldout case and requested policy")
    summaries = []
    patient_rows = []
    for policy_id in PROGRESS_POLICIES:
        policy_rows = [row for row in progress_rows if row["policy_id"] == policy_id]
        for mask_id in ["all", *sorted({row["mask_id"] for row in policy_rows})]:
            subset = [row for row in policy_rows if mask_id == "all" or row["mask_id"] == mask_id]
            summary = {
                "policy_id": policy_id, "mask_id": mask_id,
                "base_patient_count": len({(r["group_id"], r["profile_id"]) for r in subset}),
                "masked_case_count": len(subset),
                "cases_with_reference_eligible": sum(r["reference_eligible_count"] > 0 for r in subset),
                "newly_confirmed_trial_count": sum(r["newly_confirmed_count"] for r in subset),
                "ineligible_cleanup_count": sum(r["ineligible_cleanup_count"] for r in subset),
                "final_state_match_count": sum(r["state_match_count"] for r in subset),
            }
            summary.update({f"cases_with_candidate_step_{step}": sum(
                r[f"confirmed_count_step_{step}"] > 0 for r in subset) for step in range(4)})
            summaries.append(summary)
        patients = defaultdict(list)
        for row in policy_rows:
            patients[(row["group_id"], row["profile_id"])].append(row)
        for (group_id, profile_id), items in sorted(patients.items()):
            patient = {"group_id": group_id, "profile_id": profile_id, "policy_id": policy_id,
                       "mask_count": len(items),
                       "masks_with_candidate": sum(r["confirmed_count_step_3"] > 0 for r in items)}
            for item in sorted(items, key=lambda r: r["mask_id"]):
                patient[f"first_confirmation_mask_{item['mask_id']}"] = item["first_confirmation_step"]
                patient[f"newly_confirmed_mask_{item['mask_id']}"] = item["newly_confirmed_count"]
            patient_rows.append(patient)
    _write_csv(output_dir / "candidate_progress_cases.csv", progress_rows)
    _write_csv(output_dir / "candidate_progress_summary.csv", summaries)
    _write_csv(output_dir / "candidate_progress_patients.csv", patient_rows)
    return {"evaluation": "saved three-action trajectories, not separate budget reruns",
            "random_order": "not reconstructed from its mean; individual trajectories are not saved",
            "summary": summaries}


def build_structure_examples(output_dir: Path) -> list[dict[str, Any]]:
    """Run fixed examples from the existing generator, not another benchmark."""
    from clarifytrial.contracts import NextAction
    from clarifytrial.interactive.oracle import evaluate_policy_view
    from clarifytrial.interactive.policies import ClarifyTrialExactCoveragePolicy, ClarifyTrialRulePolicy
    from clarifytrial.interactive.stress import build_stress_case

    examples = []
    for topology in ("fully_shared", "chain"):
        for budget in (2, 3):
            case = build_stress_case(topology, 0, seed=20260830, action_budget=budget)
            view = case.public_policy_view()
            aliases = {item.fact_id: chr(65 + int(item.fact_id.rsplit("-", 1)[1]))
                       for item in view.available_information}
            initial = evaluate_policy_view(view, case.initial_patient_state())
            missing = {decision.trial_id: sorted({aliases[fact_id]
                       for assessment in decision.criterion_assessments
                       for fact_id in assessment.missing_information_ids})
                       for decision in initial.decisions}
            answers = {item.request.fact_id: item.answer for item in case.hidden_facts}
            for policy in (ClarifyTrialRulePolicy(), ClarifyTrialExactCoveragePolicy()):
                state = case.initial_patient_state()
                selected, routes = [], []
                for _ in range(budget):
                    action = policy.select(view, evaluate_policy_view(view, state), frozenset(selected))
                    if action.action in {NextAction.NONE, NextAction.DEFER}:
                        break
                    answer = answers[action.target_fact_id]
                    if answer.access_path is not action.action:
                        raise ValueError("example selected an unavailable route")
                    selected.append(action.target_fact_id)
                    routes.append(action.action.value)
                    state = state.model_copy(update={"facts": [*state.facts, answer.evidence]})
                path = _candidate_trajectory(case, selected)
                examples.append({
                    "topology": topology, "structure_number": 0, "seed": 20260830,
                    "action_budget": budget, "policy_id": policy.policy_id,
                    "required_information": missing,
                    "synthetic_answers": {aliases[key]: value.evidence.value for key, value in answers.items()},
                    "selected_information": [aliases[key] for key in selected],
                    "selected_actions": routes,
                    "confirmed_trial_ids": path[-1]["reference_confirmed_ids"],
                    "confirmed_counts_by_step": [len(step["reference_confirmed_ids"]) for step in path],
                })
    _write_json(output_dir / "structure_examples.json", examples)
    return examples


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the KOSMI poster transition measurement tables."
    )
    parser.add_argument(
        "--transition-root",
        type=Path,
        default=Path("runs/kosmi-transition-balance-20260902"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/internal/results/kosmi-poster-evidence-v1"),
    )
    parser.add_argument("--candidate-progress", action="store_true",
                        help="Add candidate-progress tables and small structure examples; keep existing tables unchanged.")
    parser.add_argument("--config", type=Path, default=Path("configs/interactive_public_benchmark_v1.json"))
    args = parser.parse_args()
    if args.candidate_progress:
        payload = build_supplement(args.transition_root, args.output, args.config)
        build_structure_examples(args.output)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    payload = build(args.transition_root, args.output)
    print(args.output / "summary.json")
    print(json.dumps(payload["scope"], ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
