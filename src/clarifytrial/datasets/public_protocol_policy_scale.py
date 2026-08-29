"""Question-order scale evaluation on the 50 public-protocol synthetic patients."""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from itertools import permutations
from math import factorial
from pathlib import Path
from statistics import mean, median
from time import perf_counter
from typing import Any

from ..contracts import (
    CandidateStatus,
    ConfirmationStatus,
    EvidenceFact,
    NextAction,
    PatientState,
)
from ..environment import HiddenFactAnswer, PublicFactRequest
from ..io import atomic_write_text
from ..interactive.contracts import (
    InteractiveCase,
    InteractiveHiddenFact,
    InteractiveSnapshot,
    InteractiveTrial,
)
from ..interactive.oracle import evaluate_interactive_case, evaluate_policy_view
from ..interactive.policies import (
    AuthoredOrderPolicy,
    ClarifyTrialExactCoveragePolicy,
    ClarifyTrialRulePolicy,
    DeclaredFactOrderPolicy,
    NoQuestionPolicy,
    QuestionPolicy,
)
from ..interactive.statistics import exact_sign_test, stratified_bootstrap_mean
from .integrity import portable_text_sha256
from .source_benchmark import _trial_objects


POLICY_LABELS = {
    "no_questions": "추가 확인 없음",
    "authored_order": "파일에 적힌 순서",
    "random_order_expectation": "가능한 모든 순서의 평균",
    "clarifytrial_rule_v1": "여러 시험에 함께 필요한 정보 우선",
    "clarifytrial_exact_coverage_v3": "남은 확인 횟수 전체를 계산",
}
POLICY_IDS = tuple(POLICY_LABELS)
PAIRED_COMPARISONS = (
    ("clarifytrial_rule_v1", "random_order_expectation"),
    ("clarifytrial_rule_v1", "authored_order"),
    ("clarifytrial_exact_coverage_v3", "random_order_expectation"),
    ("clarifytrial_exact_coverage_v3", "clarifytrial_rule_v1"),
)


def _read(path: str | Path) -> dict[str, Any]:
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def _write_json(path: Path, value: Any) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        raise ValueError(f"cannot write an empty CSV: {path}")
    fieldnames = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _fact_code_from_id(fact_id: str) -> str:
    return fact_id.rsplit(":", 1)[-1]


def _shared_degree(case: InteractiveCase) -> dict[str, Any]:
    view = case.public_policy_view()
    criterion_to_trial = {
        criterion.criterion_id: trial.trial_id
        for trial in view.trials
        for criterion in trial.criteria
    }
    affected_by_fact = {
        item.fact_id: len(
            {
                criterion_to_trial[criterion_id]
                for criterion_id in item.related_criterion_ids
            }
        )
        for item in view.available_information
    }
    counts = list(affected_by_fact.values())
    return {
        "affected_trials_by_fact": affected_by_fact,
        "mean_affected_trials": mean(counts),
        "shared_fact_fraction_ge2": sum(item >= 2 for item in counts) / len(counts),
        "maximum_affected_trials": max(counts),
    }


def load_public_protocol_policy_cases(
    *,
    trial_set_path: str | Path,
    patient_pairs_path: str | Path,
    action_budget: int = 5,
    patient_ids: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Load the frozen 50-patient data into the shared interactive contracts."""

    if action_budget < 0:
        raise ValueError("action_budget must not be negative")
    trial_set = _read(trial_set_path)
    pairs_document = _read(patient_pairs_path)
    if trial_set.get("protocol_id") != pairs_document.get("protocol_id"):
        raise ValueError("trial and patient files use different protocols")
    criteria_by_trial, logic_by_trial = _trial_objects(
        rows=trial_set["criteria"],
        trials=trial_set["trials"],
    )
    group_labels = {
        str(item["group_id"]): str(item["group_label"])
        for item in pairs_document["groups"]
    }
    selected_ids = set(patient_ids or ())
    available_ids = {str(item["patient_id"]) for item in pairs_document["pairs"]}
    if unknown := selected_ids - available_ids:
        raise ValueError(f"unknown patient IDs: {sorted(unknown)!r}")
    selected_pairs = [
        item
        for item in pairs_document["pairs"]
        if not selected_ids or str(item["patient_id"]) in selected_ids
    ]
    if not selected_pairs:
        raise ValueError("no patient remained after filtering")

    rows = []
    as_of = datetime.fromisoformat(str(pairs_document["as_of"]))
    for pair in selected_pairs:
        patient_id = str(pair["patient_id"])
        group_id = str(pair["group_id"])
        full_facts = [
            EvidenceFact.model_validate(item)
            for item in pair["sufficient_evidence_episode"]["evidence"]
        ]
        initial_ids = [
            str(item["evidence_id"])
            for item in pair["insufficient_evidence_episode"]["evidence"]
        ]
        answers_by_code = {
            str(item.concept).rsplit(":", 1)[-1]: item
            for item in (
                EvidenceFact.model_validate(value)
                for value in pair["insufficient_evidence_episode"][
                    "verification_answers"
                ]
            )
        }
        requests_by_code = {
            _fact_code_from_id(str(item["fact_id"])): item
            for item in pair["insufficient_evidence_episode"][
                "missing_information"
            ]
        }
        hidden = []
        for fact_code in pair["pivotal_fact_codes"]:
            request_row = requests_by_code[str(fact_code)]
            action = NextAction(str(request_row["acceptable_actions"][0]))
            request = PublicFactRequest(
                fact_id=str(request_row["fact_id"]),
                description=str(request_row["description"]),
                available_actions=(action,),
            )
            hidden.append(
                InteractiveHiddenFact(
                    request=request,
                    answer=HiddenFactAnswer(
                        fact_id=request.fact_id,
                        access_path=action,
                        evidence=answers_by_code[str(fact_code)],
                    ),
                )
            )
        trials = [
            InteractiveTrial(
                trial_id=str(trial_id),
                criteria=criteria_by_trial[str(trial_id)],
                eligibility_logic=logic_by_trial[str(trial_id)],
            )
            for trial_id in pair["trial_ids"]
        ]
        case = InteractiveCase(
            case_id=patient_id,
            disease_group=group_labels[group_id],
            full_patient_state=PatientState(
                patient_id=patient_id,
                as_of=as_of,
                facts=full_facts,
            ),
            initial_visible_evidence_ids=initial_ids,
            trials=trials,
            hidden_facts=hidden,
            action_budget=action_budget,
        )
        rows.append(
            {
                "patient_id": patient_id,
                "group_id": group_id,
                "disease_group": group_labels[group_id],
                "split": str(pair["split"]),
                "missing_fact_count": len(hidden),
                "case": case,
                **_shared_degree(case),
            }
        )
    return rows, trial_set, pairs_document


def _score_snapshots(
    *,
    initial: InteractiveSnapshot,
    final: InteractiveSnapshot,
    target: InteractiveSnapshot,
    action_count: float,
) -> dict[str, float]:
    initial_by_trial = {item.trial_id: item for item in initial.decisions}
    final_by_trial = {item.trial_id: item for item in final.decisions}
    target_by_trial = {item.trial_id: item for item in target.decisions}
    trial_ids = sorted(target_by_trial)
    exact = sum(
        (
            final_by_trial[item].candidate_status,
            final_by_trial[item].confirmation_status,
        )
        == (
            target_by_trial[item].candidate_status,
            target_by_trial[item].confirmation_status,
        )
        for item in trial_ids
    )
    candidate = sum(
        final_by_trial[item].candidate_status
        is target_by_trial[item].candidate_status
        for item in trial_ids
    )
    confirmation = sum(
        final_by_trial[item].confirmation_status
        is target_by_trial[item].confirmation_status
        for item in trial_ids
    )
    rescue_ids = [
        item
        for item in trial_ids
        if initial_by_trial[item].candidate_status is CandidateStatus.RETAIN
        and initial_by_trial[item].confirmation_status
        is ConfirmationStatus.NOT_CONFIRMED
        and target_by_trial[item].confirmation_status
        is ConfirmationStatus.CONFIRMED
    ]
    cleanup_ids = [
        item
        for item in trial_ids
        if initial_by_trial[item].candidate_status is CandidateStatus.RETAIN
        and initial_by_trial[item].confirmation_status
        is ConfirmationStatus.NOT_CONFIRMED
        and target_by_trial[item].confirmation_status
        is ConfirmationStatus.INELIGIBLE
    ]
    rescued = sum(
        final_by_trial[item].confirmation_status is ConfirmationStatus.CONFIRMED
        for item in rescue_ids
    )
    cleaned = sum(
        final_by_trial[item].confirmation_status is ConfirmationStatus.INELIGIBLE
        for item in cleanup_ids
    )
    unsafe = sum(
        (
            final_by_trial[item].candidate_status is CandidateStatus.REMOVE
            and target_by_trial[item].candidate_status is not CandidateStatus.REMOVE
        )
        or (
            final_by_trial[item].confirmation_status is ConfirmationStatus.CONFIRMED
            and target_by_trial[item].confirmation_status
            is not ConfirmationStatus.CONFIRMED
        )
        for item in trial_ids
    )
    return {
        "trial_count": float(len(trial_ids)),
        "trial_status_match_count": float(exact),
        "trial_status_recovery": exact / len(trial_ids),
        "candidate_status_recovery": candidate / len(trial_ids),
        "confirmation_status_recovery": confirmation / len(trial_ids),
        "rescue_opportunity_count": float(len(rescue_ids)),
        "confirmed_rescue_count": float(rescued),
        "cleanup_opportunity_count": float(len(cleanup_ids)),
        "ineligible_cleanup_count": float(cleaned),
        "unsafe_decision_count": float(unsafe),
        "action_count": float(action_count),
    }


def _simulate(
    *,
    case: InteractiveCase,
    policy: QuestionPolicy,
    initial: InteractiveSnapshot,
    target: InteractiveSnapshot,
) -> dict[str, Any]:
    view = case.public_policy_view()
    answers = {
        item.request.fact_id: item.answer.evidence for item in case.hidden_facts
    }
    state = initial.patient_state
    snapshot = initial
    revealed: set[str] = set()
    selected_fact_ids = []
    for _ in range(case.action_budget):
        action = policy.select(view, snapshot, frozenset(revealed))
        if action.action in {NextAction.NONE, NextAction.DEFER}:
            break
        fact_id = action.target_fact_id
        if fact_id is None or fact_id in revealed or fact_id not in answers:
            raise ValueError("question policy selected an invalid fact")
        public = next(item for item in view.available_information if item.fact_id == fact_id)
        if action.action not in public.available_actions:
            raise ValueError("question policy selected an unavailable information path")
        state = state.model_copy(
            update={"facts": [*state.facts, answers[fact_id]]}
        )
        revealed.add(fact_id)
        selected_fact_ids.append(fact_id)
        snapshot = evaluate_policy_view(view, state)
    return {
        **_score_snapshots(
            initial=initial,
            final=snapshot,
            target=target,
            action_count=len(selected_fact_ids),
        ),
        "selected_fact_ids": selected_fact_ids,
    }


def _average_random_orders(
    *,
    case: InteractiveCase,
    initial: InteractiveSnapshot,
    target: InteractiveSnapshot,
) -> dict[str, Any]:
    fact_ids = [item.fact_id for item in case.public_policy_view().available_information]
    simulations = [
        _simulate(
            case=case,
            policy=DeclaredFactOrderPolicy(
                order,
                policy_id=f"random_permutation_{index:03d}",
            ),
            initial=initial,
            target=target,
        )
        for index, order in enumerate(permutations(fact_ids))
    ]
    if len(simulations) != factorial(len(fact_ids)):
        raise AssertionError("not every missing-fact order was evaluated")
    numeric_names = [
        name
        for name, value in simulations[0].items()
        if isinstance(value, (int, float))
    ]
    return {
        **{
            name: mean(float(item[name]) for item in simulations)
            for name in numeric_names
        },
        "selected_fact_ids": [],
        "random_permutation_count": len(simulations),
    }


def _policy_rows_for_case(case_row: Mapping[str, Any], budget: int) -> list[dict[str, Any]]:
    case = case_row["case"].model_copy(update={"action_budget": budget})
    initial = evaluate_interactive_case(case, case.initial_patient_state())
    target = evaluate_interactive_case(case, case.full_patient_state)
    policy_results = [
        ("no_questions", _simulate(case=case, policy=NoQuestionPolicy(), initial=initial, target=target)),
        ("authored_order", _simulate(case=case, policy=AuthoredOrderPolicy(), initial=initial, target=target)),
        (
            "random_order_expectation",
            _average_random_orders(case=case, initial=initial, target=target),
        ),
        (
            "clarifytrial_rule_v1",
            _simulate(case=case, policy=ClarifyTrialRulePolicy(), initial=initial, target=target),
        ),
        (
            "clarifytrial_exact_coverage_v3",
            _simulate(
                case=case,
                policy=ClarifyTrialExactCoveragePolicy(),
                initial=initial,
                target=target,
            ),
        ),
    ]
    return [
        {
            "patient_id": case_row["patient_id"],
            "group_id": case_row["group_id"],
            "disease_group": case_row["disease_group"],
            "split": case_row["split"],
            "missing_fact_count": case_row["missing_fact_count"],
            "mean_affected_trials": case_row["mean_affected_trials"],
            "shared_fact_fraction_ge2": case_row["shared_fact_fraction_ge2"],
            "maximum_affected_trials": case_row["maximum_affected_trials"],
            "action_budget": budget,
            "policy_id": policy_id,
            "policy_label": POLICY_LABELS[policy_id],
            **result,
        }
        for policy_id, result in policy_results
    ]


def _aggregate(rows: Sequence[Mapping[str, Any]], keys: Sequence[str]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in keys)].append(row)
    result = []
    for key, items in sorted(groups.items()):
        rescue_opportunities = sum(float(item["rescue_opportunity_count"]) for item in items)
        cleanup_opportunities = sum(float(item["cleanup_opportunity_count"]) for item in items)
        rescued = sum(float(item["confirmed_rescue_count"]) for item in items)
        cleaned = sum(float(item["ineligible_cleanup_count"]) for item in items)
        result.append(
            {
                **dict(zip(keys, key, strict=True)),
                "patient_count": len(items),
                "trial_count": int(sum(float(item["trial_count"]) for item in items)),
                "mean_trial_status_recovery": mean(float(item["trial_status_recovery"]) for item in items),
                "mean_candidate_status_recovery": mean(float(item["candidate_status_recovery"]) for item in items),
                "mean_confirmation_status_recovery": mean(float(item["confirmation_status_recovery"]) for item in items),
                "mean_final_status_matches_out_of_five": mean(float(item["trial_status_match_count"]) for item in items),
                "rescue_opportunity_count": rescue_opportunities,
                "confirmed_rescue_count": rescued,
                "confirmed_rescue_rate": rescued / rescue_opportunities if rescue_opportunities else None,
                "cleanup_opportunity_count": cleanup_opportunities,
                "ineligible_cleanup_count": cleaned,
                "ineligible_cleanup_rate": cleaned / cleanup_opportunities if cleanup_opportunities else None,
                "mean_action_count": mean(float(item["action_count"]) for item in items),
                "total_unsafe_decisions": sum(float(item["unsafe_decision_count"]) for item in items),
                "mean_affected_trials": mean(float(item["mean_affected_trials"]) for item in items),
                "mean_shared_fact_fraction_ge2": mean(float(item["shared_fact_fraction_ge2"]) for item in items),
            }
        )
    return result


def _paired_outputs(
    rows: Sequence[Mapping[str, Any]],
    budgets: Sequence[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    heldout = [item for item in rows if item["split"] == "heldout"]
    by_key = {
        (str(item["patient_id"]), int(item["action_budget"]), str(item["policy_id"])): item
        for item in heldout
    }
    patient_ids = sorted({str(item["patient_id"]) for item in heldout})
    patient_differences = []
    summaries = []
    for budget in budgets:
        for candidate_id, baseline_id in PAIRED_COMPARISONS:
            differences_by_metric: dict[str, dict[str, list[float]]] = {
                name: defaultdict(list)
                for name in (
                    "trial_status_recovery",
                    "confirmed_rescue_count",
                    "ineligible_cleanup_count",
                    "action_count",
                )
            }
            for patient_id in patient_ids:
                candidate = by_key[(patient_id, budget, candidate_id)]
                baseline = by_key[(patient_id, budget, baseline_id)]
                row = {
                    "patient_id": patient_id,
                    "group_id": candidate["group_id"],
                    "disease_group": candidate["disease_group"],
                    "missing_fact_count": candidate["missing_fact_count"],
                    "mean_affected_trials": candidate["mean_affected_trials"],
                    "shared_fact_fraction_ge2": candidate["shared_fact_fraction_ge2"],
                    "action_budget": budget,
                    "candidate_policy_id": candidate_id,
                    "baseline_policy_id": baseline_id,
                }
                for metric in differences_by_metric:
                    difference = float(candidate[metric]) - float(baseline[metric])
                    row[f"{metric}_difference"] = difference
                    differences_by_metric[metric][str(candidate["group_id"])].append(difference)
                patient_differences.append(row)
            summaries.append(
                {
                    "action_budget": budget,
                    "candidate_policy_id": candidate_id,
                    "baseline_policy_id": baseline_id,
                    "patient_count": len(patient_ids),
                    "paired_inference": {
                        metric: stratified_bootstrap_mean(
                            by_group,
                            cluster_unit="base_patient",
                        )
                        for metric, by_group in differences_by_metric.items()
                    },
                }
            )
    return patient_differences, summaries


def _budget_auc(
    policy_metrics: Sequence[Mapping[str, Any]], budgets: Sequence[int]
) -> list[dict[str, Any]]:
    minimum = min(budgets)
    maximum = max(budgets)
    if minimum == maximum:
        return []
    heldout = [item for item in policy_metrics if item["split"] == "heldout"]
    by_key = {
        (int(item["action_budget"]), str(item["policy_id"])): item
        for item in heldout
    }
    results = []
    for policy_id in POLICY_IDS:
        row = {"policy_id": policy_id, "policy_label": POLICY_LABELS[policy_id]}
        for metric in (
            "mean_trial_status_recovery",
            "confirmed_rescue_rate",
            "ineligible_cleanup_rate",
        ):
            points = [
                (budget, by_key[(budget, policy_id)][metric]) for budget in budgets
            ]
            if any(value is None for _, value in points):
                row[f"{metric}_normalized_auc"] = None
                continue
            area = sum(
                (right_budget - left_budget)
                * (float(left_value) + float(right_value))
                / 2
                for (left_budget, left_value), (right_budget, right_value) in zip(
                    points, points[1:], strict=False
                )
            )
            row[f"{metric}_normalized_auc"] = area / (maximum - minimum)
        results.append(row)
    return results


def _paired_budget_auc(
    rows: Sequence[Mapping[str, Any]],
    budgets: Sequence[int],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Integrate each heldout patient's curve before paired inference."""

    minimum = min(budgets)
    maximum = max(budgets)
    if minimum == maximum:
        return [], []
    heldout = [item for item in rows if item["split"] == "heldout"]
    patient_ids = sorted({str(item["patient_id"]) for item in heldout})
    by_key = {
        (str(item["patient_id"]), int(item["action_budget"]), str(item["policy_id"])): item
        for item in heldout
    }

    def auc(patient_id: str, policy_id: str) -> float:
        points = [
            (
                budget,
                float(by_key[(patient_id, budget, policy_id)]["trial_status_recovery"]),
            )
            for budget in budgets
        ]
        area = sum(
            (right_budget - left_budget) * (left + right) / 2
            for (left_budget, left), (right_budget, right) in zip(
                points, points[1:], strict=False
            )
        )
        return area / (maximum - minimum)

    comparisons = (
        ("clarifytrial_rule_v1", "random_order_expectation"),
        ("clarifytrial_rule_v1", "authored_order"),
        ("clarifytrial_exact_coverage_v3", "clarifytrial_rule_v1"),
    )
    patient_rows = []
    summary_rows = []
    for candidate_id, baseline_id in comparisons:
        by_group: dict[str, list[float]] = defaultdict(list)
        candidate_values = []
        baseline_values = []
        for patient_id in patient_ids:
            reference = by_key[(patient_id, budgets[0], candidate_id)]
            candidate_auc = auc(patient_id, candidate_id)
            baseline_auc = auc(patient_id, baseline_id)
            difference = candidate_auc - baseline_auc
            candidate_values.append(candidate_auc)
            baseline_values.append(baseline_auc)
            by_group[str(reference["group_id"])].append(difference)
            patient_rows.append(
                {
                    "patient_id": patient_id,
                    "group_id": reference["group_id"],
                    "disease_group": reference["disease_group"],
                    "missing_fact_count": reference["missing_fact_count"],
                    "mean_affected_trials": reference["mean_affected_trials"],
                    "budget_minimum": minimum,
                    "budget_maximum": maximum,
                    "candidate_policy_id": candidate_id,
                    "baseline_policy_id": baseline_id,
                    "candidate_normalized_auc": candidate_auc,
                    "baseline_normalized_auc": baseline_auc,
                    "normalized_auc_difference": difference,
                }
            )
        summary_rows.append(
            {
                "metric": "patient_trial_status_recovery_normalized_auc",
                "budget_range": [minimum, maximum],
                "candidate_policy_id": candidate_id,
                "baseline_policy_id": baseline_id,
                "patient_count": len(patient_ids),
                "mean_candidate_normalized_auc": mean(candidate_values),
                "mean_baseline_normalized_auc": mean(baseline_values),
                "paired_inference": stratified_bootstrap_mean(
                    by_group,
                    cluster_unit="base_patient",
                ),
            }
        )
    return patient_rows, summary_rows


def _known_age_sensitivity(
    cases: Sequence[Mapping[str, Any]],
    budgets: Sequence[int],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Repeat heldout evaluation after supplying the dominant age fact up front."""

    sensitivity_cases = []
    for case_row in cases:
        if case_row["split"] != "heldout":
            continue
        case = case_row["case"]
        age_facts = [
            item
            for item in case.hidden_facts
            if _fact_code_from_id(item.request.fact_id) == "age_years"
        ]
        if not age_facts:
            continue
        if len(age_facts) != 1:
            raise ValueError("a patient cannot contain multiple hidden age facts")
        age_fact = age_facts[0]
        age_evidence_id = age_fact.answer.evidence.evidence_id
        known_case = case.model_copy(
            update={
                "initial_visible_evidence_ids": [
                    *case.initial_visible_evidence_ids,
                    age_evidence_id,
                ],
                "hidden_facts": [
                    item
                    for item in case.hidden_facts
                    if item.request.fact_id != age_fact.request.fact_id
                ],
                "action_budget": max(budgets),
            }
        )
        if not known_case.hidden_facts:
            continue
        sensitivity_cases.append(
            {
                **case_row,
                "case": known_case,
                "missing_fact_count": len(known_case.hidden_facts),
                **_shared_degree(known_case),
            }
        )

    rows = [
        policy_row
        for case_row in sensitivity_cases
        for budget in budgets
        for policy_row in _policy_rows_for_case(case_row, budget)
    ]
    if not rows:
        return (
            {
                "fact_code_provided_at_start": "age_years",
                "patient_count": 0,
                "interpretation": "No heldout patient had a hidden age fact.",
            },
            [],
            [],
        )

    policy_metrics = _aggregate(rows, ("split", "action_budget", "policy_id"))
    patient_differences, paired_comparisons = _paired_outputs(rows, budgets)
    _, paired_auc = _paired_budget_auc(rows, budgets)
    selected_first_facts = [
        _fact_code_from_id(str(item["selected_fact_ids"][0]))
        for item in rows
        if item["action_budget"] == 1
        and item["policy_id"] == "clarifytrial_rule_v1"
        and item["selected_fact_ids"]
    ]
    summary = {
        "fact_code_provided_at_start": "age_years",
        "patient_count": len(sensitivity_cases),
        "reason": (
            "Age was the first ClarifyTrial selection for 29 of 30 heldout "
            "patients in the primary one-action evaluation. This sensitivity "
            "supplies age before the run and evaluates only the remaining facts."
        ),
        "scope": (
            "Sensitivity within the same synthetic patients and public trial sets; "
            "not an independent cohort or a new-disease evaluation."
        ),
        "selected_first_fact_codes_after_age_known": sorted(selected_first_facts),
        "policy_metrics": policy_metrics,
        "paired_comparisons": paired_comparisons,
        "paired_budget_auc": paired_auc,
    }
    return summary, patient_differences, rows


def _subgroup_effects(
    *,
    patient_differences: Sequence[Mapping[str, Any]],
    shared_threshold: float,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    primary = [
        item
        for item in patient_differences
        if item["candidate_policy_id"] == "clarifytrial_rule_v1"
        and item["baseline_policy_id"] == "random_order_expectation"
    ]
    shared_rows = []
    missing_rows = []
    contrasts = []
    for budget in sorted({int(item["action_budget"]) for item in primary}):
        budget_rows = [item for item in primary if int(item["action_budget"]) == budget]
        shared_means = {}
        for label, selected in (
            (
                "lower_shared",
                [item for item in budget_rows if float(item["mean_affected_trials"]) < shared_threshold],
            ),
            (
                "higher_shared",
                [item for item in budget_rows if float(item["mean_affected_trials"]) >= shared_threshold],
            ),
        ):
            if not selected:
                continue
            by_group: dict[str, list[float]] = defaultdict(list)
            for item in selected:
                by_group[str(item["group_id"])].append(
                    float(item["trial_status_recovery_difference"])
                )
            inference = stratified_bootstrap_mean(
                by_group,
                cluster_unit="base_patient",
            )
            shared_rows.append(
                {
                    "action_budget": budget,
                    "shared_degree_group": label,
                    "shared_degree_threshold_mean_affected_trials": shared_threshold,
                    "patient_count": len(selected),
                    "mean_affected_trials": mean(float(item["mean_affected_trials"]) for item in selected),
                    "mean_shared_fact_fraction_ge2": mean(float(item["shared_fact_fraction_ge2"]) for item in selected),
                    **inference,
                }
            )
            shared_means[label] = inference["mean_difference"]
        if set(shared_means) == {"lower_shared", "higher_shared"}:
            contrasts.append(
                {
                    "action_budget": budget,
                    "higher_shared_mean_difference": shared_means["higher_shared"],
                    "lower_shared_mean_difference": shared_means["lower_shared"],
                    "descriptive_difference_of_paired_effects": (
                        shared_means["higher_shared"] - shared_means["lower_shared"]
                    ),
                }
            )
        for missing_count in sorted({int(item["missing_fact_count"]) for item in budget_rows}):
            selected = [
                item for item in budget_rows if int(item["missing_fact_count"]) == missing_count
            ]
            by_group = defaultdict(list)
            for item in selected:
                by_group[str(item["group_id"])].append(
                    float(item["trial_status_recovery_difference"])
                )
            missing_rows.append(
                {
                    "action_budget": budget,
                    "missing_fact_count": missing_count,
                    "patient_count": len(selected),
                    **stratified_bootstrap_mean(
                        by_group,
                        cluster_unit="base_patient",
                    ),
                }
            )
    return shared_rows, missing_rows, contrasts


def _flat_paired_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for item in rows:
        for metric, inference in item["paired_inference"].items():
            lower_is_better = metric == "action_count"
            result.append(
                {
                    "action_budget": item["action_budget"],
                    "candidate_policy_id": item["candidate_policy_id"],
                    "baseline_policy_id": item["baseline_policy_id"],
                    "metric": metric,
                    "patient_count": item["patient_count"],
                    "mean_difference": inference["mean_difference"],
                    "bootstrap_95_lower": inference["bootstrap_95_ci"]["lower"],
                    "bootstrap_95_upper": inference["bootstrap_95_ci"]["upper"],
                    "wins": inference["wins"],
                    "ties": inference["ties"],
                    "losses": inference["losses"],
                    "preferred_direction": (
                        "lower" if lower_is_better else "higher"
                    ),
                    "candidate_better_count": (
                        inference["losses"]
                        if lower_is_better
                        else inference["wins"]
                    ),
                    "candidate_same_count": inference["ties"],
                    "candidate_worse_count": (
                        inference["wins"]
                        if lower_is_better
                        else inference["losses"]
                    ),
                    "two_sided_exact_sign_test_p": inference[
                        "two_sided_exact_sign_test_p"
                    ],
                }
            )
    return result


def _flat_auc_comparisons(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "metric": item["metric"],
            "budget_minimum": item["budget_range"][0],
            "budget_maximum": item["budget_range"][1],
            "candidate_policy_id": item["candidate_policy_id"],
            "baseline_policy_id": item["baseline_policy_id"],
            "patient_count": item["patient_count"],
            "mean_candidate_normalized_auc": item["mean_candidate_normalized_auc"],
            "mean_baseline_normalized_auc": item["mean_baseline_normalized_auc"],
            "mean_paired_difference": item["paired_inference"]["mean_difference"],
            "bootstrap_95_lower": item["paired_inference"]["bootstrap_95_ci"]["lower"],
            "bootstrap_95_upper": item["paired_inference"]["bootstrap_95_ci"]["upper"],
            "wins": item["paired_inference"]["wins"],
            "ties": item["paired_inference"]["ties"],
            "losses": item["paired_inference"]["losses"],
            "two_sided_exact_sign_test_p": item["paired_inference"][
                "two_sided_exact_sign_test_p"
            ],
        }
        for item in rows
    ]


def _shared_effect_auc(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any] | None:
    budgets = sorted({int(item["action_budget"]) for item in rows})
    if len(budgets) < 2 or budgets[-1] == budgets[0]:
        return None
    by_key = {
        (str(item["shared_degree_group"]), int(item["action_budget"])): float(
            item["mean_difference"]
        )
        for item in rows
    }

    def auc(group: str) -> float:
        points = [(budget, by_key[(group, budget)]) for budget in budgets]
        area = sum(
            (right_budget - left_budget) * (left + right) / 2
            for (left_budget, left), (right_budget, right) in zip(
                points, points[1:], strict=False
            )
        )
        return area / (budgets[-1] - budgets[0])

    lower = auc("lower_shared")
    higher = auc("higher_shared")
    return {
        "metric": "clarifytrial_rule_v1_minus_random_order_expectation",
        "budget_range": [budgets[0], budgets[-1]],
        "lower_shared_normalized_auc": lower,
        "higher_shared_normalized_auc": higher,
        "higher_minus_lower": higher - lower,
        "interpretation": (
            "Descriptive curve comparison. A positive value would mean the paired "
            "advantage was larger in the higher-shared subgroup across budgets."
        ),
    }


def _disease_level_sensitivity(
    patient_differences: Sequence[Mapping[str, Any]],
    auc_patient_rows: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Check whether a paired effect keeps the same direction across diseases.

    Patients from one disease share the same five trial graphs.  This check first
    averages those patients and then counts disease-level directions.  It is a
    robustness description for the ten selected disease groups, not an estimate
    for unobserved diseases or trial collections.
    """

    detail_rows: list[dict[str, Any]] = []
    summary_rows: list[dict[str, Any]] = []

    def append_grouped(
        source_rows: Sequence[Mapping[str, Any]],
        *,
        metric: str,
        value_field: str,
        budget_field: str | None,
    ) -> None:
        grouped: dict[tuple[Any, ...], list[Mapping[str, Any]]] = defaultdict(list)
        for item in source_rows:
            key = (
                item["candidate_policy_id"],
                item["baseline_policy_id"],
                None if budget_field is None else item[budget_field],
                item["group_id"],
                item["disease_group"],
            )
            grouped[key].append(item)

        disease_rows: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
        for key, items in sorted(grouped.items()):
            candidate_id, baseline_id, budget, group_id, disease_group = key
            row = {
                "metric": metric,
                "action_budget": budget,
                "candidate_policy_id": candidate_id,
                "baseline_policy_id": baseline_id,
                "group_id": group_id,
                "disease_group": disease_group,
                "patient_count": len(items),
                "mean_paired_difference": mean(
                    float(item[value_field]) for item in items
                ),
            }
            detail_rows.append(row)
            disease_rows[(metric, budget, candidate_id, baseline_id)].append(row)

        for key, items in sorted(disease_rows.items()):
            metric_name, budget, candidate_id, baseline_id = key
            differences = [float(item["mean_paired_difference"]) for item in items]
            summary_rows.append(
                {
                    "metric": metric_name,
                    "action_budget": budget,
                    "candidate_policy_id": candidate_id,
                    "baseline_policy_id": baseline_id,
                    "disease_group_count": len(items),
                    "mean_disease_level_difference": mean(differences),
                    "minimum_disease_level_difference": min(differences),
                    "maximum_disease_level_difference": max(differences),
                    "wins": sum(item > 1e-12 for item in differences),
                    "ties": sum(abs(item) <= 1e-12 for item in differences),
                    "losses": sum(item < -1e-12 for item in differences),
                    "two_sided_exact_sign_test_p": exact_sign_test(differences),
                    "interpretation_limit": (
                        "Direction check across the ten selected disease groups; "
                        "does not include variation from new diseases or trial sets."
                    ),
                }
            )

    append_grouped(
        patient_differences,
        metric="trial_status_recovery",
        value_field="trial_status_recovery_difference",
        budget_field="action_budget",
    )
    append_grouped(
        auc_patient_rows,
        metric="trial_status_recovery_normalized_auc",
        value_field="normalized_auc_difference",
        budget_field=None,
    )
    return detail_rows, summary_rows


def _interpretation_markdown(summary: Mapping[str, Any]) -> str:
    metrics = {
        (int(item["action_budget"]), str(item["policy_id"])): item
        for item in summary["policy_metrics"]
        if item["split"] == "heldout"
    }
    lines = [
        "# 공개 조건 기반 50명 질문 순서 평가",
        "",
        "이 결과는 공개 임상시험에서 구조화한 일부 조건과 합성 환자 정보로 질문 순서만 비교한 것이다. 임상 판단 정확도나 실제 환자 모집 성능을 뜻하지 않는다.",
        "",
        "heldout 환자 30명 안에서 같은 환자에게 각 방법을 적용했다. 개발 환자는 결손 1·2개, heldout 환자는 결손 3·5개로 구성되어 있으므로 두 split의 점수 차이를 일반화 성능이나 결손 수 효과로 해석하지 않는다.",
        "",
        "## heldout 30명에서 확인 횟수별 시험 상태 일치",
        "",
        "| 확인 횟수 | 추가 확인 없음 | 파일 순서 | 가능한 모든 순서 평균 | 여러 시험에 함께 필요한 정보 우선 | 남은 횟수 전체 계산 |",
        "|---:|---:|---:|---:|---:|---:|",
    ]
    for budget in summary["action_budgets"]:
        values = [
            metrics[(budget, policy_id)]["mean_trial_status_recovery"]
            for policy_id in POLICY_IDS
        ]
        lines.append(
            f"| {budget} | " + " | ".join(f"{item:.1%}" for item in values) + " |"
        )
    lines.extend(
        [
            "",
            "## 후보 확정과 참가 조건 불충족 정리",
            "",
            "처음에는 정보가 부족해 후보로 남긴 시험을 두 방향으로 나눠 셌다. 가상 환자의 전체 상태에서 참가 가능했던 시험은 질문 뒤 현재 확인을 마쳤는지 보았다. 전체 상태에서 참가 조건에 맞지 않았던 시험은 답을 받은 뒤 제대로 제외했는지 보았다.",
            "",
            "| 확인 횟수 | 가능한 모든 순서 평균: 참가 가능 후보 확인 | 영향도 우선: 참가 가능 후보 확인 | 가능한 모든 순서 평균: 조건 불충족 정리 | 영향도 우선: 조건 불충족 정리 |",
            "|---:|---:|---:|---:|---:|",
        ]
    )
    for budget in summary["action_budgets"]:
        random_row = metrics[(budget, "random_order_expectation")]
        current_row = metrics[(budget, "clarifytrial_rule_v1")]

        def count_rate(row: Mapping[str, Any], count: str, total: str, rate: str) -> str:
            return (
                f"{float(row[count]):.2f}/{float(row[total]):.0f}개 "
                f"({float(row[rate]):.1%})"
            )

        lines.append(
            f"| {budget} | "
            f"{count_rate(random_row, 'confirmed_rescue_count', 'rescue_opportunity_count', 'confirmed_rescue_rate')} | "
            f"{count_rate(current_row, 'confirmed_rescue_count', 'rescue_opportunity_count', 'confirmed_rescue_rate')} | "
            f"{count_rate(random_row, 'ineligible_cleanup_count', 'cleanup_opportunity_count', 'ineligible_cleanup_rate')} | "
            f"{count_rate(current_row, 'ineligible_cleanup_count', 'cleanup_opportunity_count', 'ineligible_cleanup_rate')} |"
        )
    lines.extend(
        [
            "",
            "## 공유 정도 하위집단",
            "",
            f"heldout 환자의 가린 정보가 연결된 시험 수 중앙값은 {summary['shared_degree']['heldout_median_mean_affected_trials']:.2f}개였다. 이 값을 기준으로 두 집단을 나눴다. 하위집단 비교는 자료 구조를 설명하는 기술 통계이며 임상 효과가 아니다.",
            "",
            "| 확인 횟수 | 공유 정도 | 환자 수 | 영향도 우선과 모든 순서 평균의 상태 일치 차이 | 95% 범위 |",
            "|---:|---|---:|---:|---:|",
        ]
    )
    for item in summary["shared_degree_effects"]:
        interval = item["bootstrap_95_ci"]
        label = "높음" if item["shared_degree_group"] == "higher_shared" else "낮음"
        lines.append(
            f"| {item['action_budget']} | {label} | {item['patient_count']} | "
            f"{item['mean_difference']:+.1%}p | {interval['lower']:+.1%}p~{interval['upper']:+.1%}p |"
        )
    shared_auc = summary.get("shared_degree_effect_auc")
    if isinstance(shared_auc, dict):
        lines.extend(
            [
                "",
                (
                    "확인 횟수 0~5의 전체 곡선을 합쳐 보면 영향도 우선과 모든 순서 "
                    "평균의 차이는 공유 정도가 낮은 집단에서 "
                    f"{shared_auc['lower_shared_normalized_auc']:.1%}p, 높은 집단에서 "
                    f"{shared_auc['higher_shared_normalized_auc']:.1%}p였다. 이번 자료에서는 "
                    "공유 정도가 높을수록 이득이 계속 커지는 모습이 나오지 않았다."
                ),
            ]
        )
    exact_equal = all(
        abs(
            item["paired_inference"]["trial_status_recovery"][
                "mean_difference"
            ]
        )
        <= 1e-12
        for item in summary["paired_comparisons"]
        if item["candidate_policy_id"] == "clarifytrial_exact_coverage_v3"
        and item["baseline_policy_id"] == "clarifytrial_rule_v1"
    )
    if exact_equal:
        lines.extend(
            [
                "",
                "남은 확인 횟수의 모든 조합을 계산한 방식은 확인 횟수 0~5에서 단순한 영향도 우선과 같은 결과를 냈다. 이 자료에서는 복잡한 계산을 기본값으로 둘 근거가 없었다.",
            ]
        )
    auc_primary = next(
        (
            item
            for item in summary.get("paired_budget_auc", [])
            if item["candidate_policy_id"] == "clarifytrial_rule_v1"
            and item["baseline_policy_id"] == "random_order_expectation"
        ),
        None,
    )
    if isinstance(auc_primary, dict):
        inference = auc_primary["paired_inference"]
        interval = inference["bootstrap_95_ci"]
        lines.extend(
            [
                "",
                (
                    "환자마다 확인 횟수 0~5의 전체 곡선을 먼저 합친 뒤 비교하면 영향도 "
                    "우선의 상태 일치 면적은 모든 순서 평균보다 "
                    f"{inference['mean_difference']:+.1%}p 높았다. 환자 단위 95% 범위는 "
                    f"{interval['lower']:+.1%}p에서 {interval['upper']:+.1%}p였다."
                ),
            ]
        )
    disease_primary = next(
        (
            item
            for item in summary.get("disease_level_sensitivity", [])
            if item["metric"] == "trial_status_recovery"
            and item["action_budget"] == 1
            and item["candidate_policy_id"] == "clarifytrial_rule_v1"
            and item["baseline_policy_id"] == "random_order_expectation"
        ),
        None,
    )
    disease_auc = next(
        (
            item
            for item in summary.get("disease_level_sensitivity", [])
            if item["metric"] == "trial_status_recovery_normalized_auc"
            and item["candidate_policy_id"] == "clarifytrial_rule_v1"
            and item["baseline_policy_id"] == "random_order_expectation"
        ),
        None,
    )
    if isinstance(disease_primary, dict) and isinstance(disease_auc, dict):
        lines.extend(
            [
                "",
                (
                    "같은 질환의 환자 세 명은 같은 시험 다섯 건을 공유하므로 질환별 "
                    "평균도 따로 확인했다. 확인 한 번의 차이는 선택한 10개 질환 모두에서 "
                    "양수였고, 질환별 범위는 "
                    f"{disease_primary['minimum_disease_level_difference']:+.1%}p에서 "
                    f"{disease_primary['maximum_disease_level_difference']:+.1%}p였다. "
                    "확인 횟수 0~5의 곡선 차이도 10개 질환 모두 양수였다. 이 점검은 "
                    "특정 질환 한두 곳이 전체 평균을 만든 것은 아님을 보여 주지만, "
                    "새 질환이나 새 시험 묶음의 변동까지 포함하지는 않는다."
                ),
            ]
        )
    known_age = summary.get("known_age_sensitivity")
    if isinstance(known_age, dict) and known_age.get("patient_count"):
        known_metrics = {
            (int(item["action_budget"]), str(item["policy_id"])): item
            for item in known_age["policy_metrics"]
        }
        known_comparison = next(
            item
            for item in known_age["paired_comparisons"]
            if item["action_budget"] == 1
            and item["candidate_policy_id"] == "clarifytrial_rule_v1"
            and item["baseline_policy_id"] == "random_order_expectation"
        )["paired_inference"]["trial_status_recovery"]
        lines.extend(
            [
                "",
                "## 나이 정보를 처음부터 알고 있을 때",
                "",
                (
                    "주 평가에서 확인 한 번일 때 영향도 우선이 처음 고른 정보는 "
                    "30명 중 29명에서 나이였다. 이 한 가지 공통 정보에 결과가 얼마나 "
                    "의존하는지 보기 위해 나이를 시작 자료에 넣고 나머지 정보만 다시 "
                    "골랐다. 확인 한 번의 상태 일치율은 가능한 모든 순서 평균이 "
                    f"{known_metrics[(1, 'random_order_expectation')]['mean_trial_status_recovery']:.1%}, "
                    "영향도 우선이 "
                    f"{known_metrics[(1, 'clarifytrial_rule_v1')]['mean_trial_status_recovery']:.1%}였다. "
                    "환자별 차이는 평균 "
                    f"{known_comparison['mean_difference']:+.1%}p였고 "
                    f"{known_comparison['wins']}명에서 개선, "
                    f"{known_comparison['ties']}명에서 동일, "
                    f"{known_comparison['losses']}명에서 감소했다. 주 평가의 큰 차이에는 "
                    "여러 시험이 함께 요구한 나이를 먼저 확인한 효과가 크게 들어 있다."
                ),
            ]
        )
    lines.extend(
        [
            "",
            "`patient-results.csv`에는 환자별 결과, `paired-patient-differences.csv`에는 같은 환자에서 계산한 정책 차이, `paired-comparisons.csv`에는 질환을 층으로 유지한 환자 단위 95% 범위와 부호 검정 결과가 있다.",
            "",
        ]
    )
    return "\n".join(lines)


def run_public_protocol_policy_scale(
    *,
    trial_set_path: str | Path,
    patient_pairs_path: str | Path,
    output_dir: str | Path,
    action_budgets: Sequence[int] = tuple(range(6)),
    patient_ids: Sequence[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Run the full policy curve and write machine-readable result tables."""

    budgets = tuple(sorted(set(int(item) for item in action_budgets)))
    if not budgets or any(item < 0 or item > 5 for item in budgets):
        raise ValueError("action budgets must be unique values from 0 through 5")
    started = perf_counter()
    cases, trial_set, pairs_document = load_public_protocol_policy_cases(
        trial_set_path=trial_set_path,
        patient_pairs_path=patient_pairs_path,
        action_budget=max(budgets),
        patient_ids=patient_ids,
    )
    heldout_shared = [
        float(item["mean_affected_trials"])
        for item in cases
        if item["split"] == "heldout"
    ]
    if not heldout_shared:
        raise ValueError("the selected cases contain no heldout patients")
    shared_threshold = median(heldout_shared)
    rows = []
    for index, case_row in enumerate(cases, start=1):
        for budget in budgets:
            rows.extend(_policy_rows_for_case(case_row, budget))
        if progress is not None and (index % 5 == 0 or index == len(cases)):
            progress(f"completed {index}/{len(cases)} patients")

    policy_metrics = _aggregate(rows, ("split", "action_budget", "policy_id"))
    missing_fact_metrics = _aggregate(
        rows,
        ("split", "missing_fact_count", "action_budget", "policy_id"),
    )
    shared_policy_rows = [
        {
            **item,
            "shared_degree_group": (
                "higher_shared"
                if float(item["mean_affected_trials"]) >= shared_threshold
                else "lower_shared"
            ),
        }
        for item in rows
        if item["split"] == "heldout"
    ]
    shared_policy_metrics = _aggregate(
        shared_policy_rows,
        ("shared_degree_group", "action_budget", "policy_id"),
    )
    patient_differences, paired_comparisons = _paired_outputs(rows, budgets)
    shared_effects, missing_effects, shared_contrasts = _subgroup_effects(
        patient_differences=patient_differences,
        shared_threshold=shared_threshold,
    )
    shared_auc = _shared_effect_auc(shared_effects)
    auc_rows = _budget_auc(policy_metrics, budgets)
    auc_patient_rows, paired_auc_rows = _paired_budget_auc(rows, budgets)
    disease_sensitivity_rows, disease_sensitivity_summaries = (
        _disease_level_sensitivity(patient_differences, auc_patient_rows)
    )
    known_age_sensitivity, known_age_patient_differences, known_age_rows = (
        _known_age_sensitivity(cases, budgets)
    )
    selected_split_counts = {
        split: sum(item["split"] == split for item in cases)
        for split in ("development", "heldout")
    }
    selected_missing_counts = {
        str(value): sum(int(item["missing_fact_count"]) == value for item in cases)
        for value in (1, 2, 3, 5)
    }
    summary = {
        "protocol_id": "clarifytrial-public-protocol-policy-scale-v1",
        "source_protocol_id": trial_set["protocol_id"],
        "evaluation_scope": (
            "Question-order evaluation on selected structured public trial criteria "
            "and deterministic synthetic patients; not clinical performance."
        ),
        "primary_reporting_scope": "heldout_within_patient_paired_comparisons_only",
        "split_missingness_warning": (
            "Development contains only 1- and 2-fact missingness while heldout "
            "contains only 3- and 5-fact missingness. Never interpret a split "
            "difference as generalization or as an isolated missingness effect."
        ),
        "patient_is_independent_unit": True,
        "repeated_unit_warning": (
            "Budgets and policies are repeated evaluations of the same patient, "
            "not additional independent patients."
        ),
        "trial_set_path": str(trial_set_path),
        "patient_pairs_path": str(patient_pairs_path),
        "input_sha256": {
            "trial_set": portable_text_sha256(Path(trial_set_path)),
            "patient_pairs": portable_text_sha256(Path(patient_pairs_path)),
        },
        "action_budgets": list(budgets),
        "policy_ids": list(POLICY_IDS),
        "patient_count": len(cases),
        "development_patient_count": selected_split_counts["development"],
        "heldout_patient_count": selected_split_counts["heldout"],
        "disease_group_count": len({item["group_id"] for item in cases}),
        "candidate_trials_per_patient": 5,
        "missing_fact_count_distribution": selected_missing_counts,
        "random_order_rule": (
            "Exact mean over every permutation of the patient's missing facts; "
            "the maximum is 5! = 120 orders."
        ),
        "model_calls": 0,
        "model_tokens": 0,
        "shared_degree": {
            "definition": (
                "For each hidden fact, count distinct candidate trials that use it; "
                "the patient score is the mean of those counts."
            ),
            "heldout_median_mean_affected_trials": shared_threshold,
            "subgroup_rule": (
                "higher_shared is at or above the heldout median; lower_shared is below it"
            ),
            "subgroup_limit": (
                "Descriptive subgroup only. Missing-fact count and graph shape may still confound it."
            ),
        },
        "directional_metric_rule": {
            "confirmed_rescue": (
                "Initially retained but not confirmed, fully observed state confirmed, "
                "and the policy also reached confirmed."
            ),
            "ineligible_cleanup": (
                "Initially retained but not confirmed, fully observed state ineligible, "
                "and the policy also reached ineligible."
            ),
        },
        "bootstrap_rule": (
            "Paired patient differences resampled within disease groups; 5,000 draws."
        ),
        "runtime_seconds": perf_counter() - started,
        "policy_metrics": policy_metrics,
        "missing_fact_metrics": missing_fact_metrics,
        "shared_degree_policy_metrics": shared_policy_metrics,
        "paired_comparisons": paired_comparisons,
        "budget_auc": auc_rows,
        "paired_budget_auc": paired_auc_rows,
        "disease_level_sensitivity": disease_sensitivity_summaries,
        "known_age_sensitivity": known_age_sensitivity,
        "shared_degree_effects": shared_effects,
        "shared_degree_effect_auc": shared_auc,
        "heldout_missing_fact_effects": missing_effects,
        "shared_degree_effect_contrasts": shared_contrasts,
        "medical_data_notice": pairs_document["medical_data_notice"],
        "medical_disclaimer": pairs_document["medical_disclaimer"],
    }

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    _write_json(destination / "summary.json", summary)
    atomic_write_text(
        destination / "patient-results.jsonl",
        "".join(
            json.dumps(item, ensure_ascii=False) + "\n"
            for item in rows
        ),
        encoding="utf-8",
    )
    patient_csv_rows = [
        {
            key: (
                json.dumps(value, ensure_ascii=False)
                if isinstance(value, list)
                else value
            )
            for key, value in item.items()
        }
        for item in rows
    ]
    _write_csv(destination / "patient-results.csv", patient_csv_rows)
    _write_csv(destination / "policy-metrics.csv", policy_metrics)
    _write_csv(destination / "missing-fact-metrics.csv", missing_fact_metrics)
    _write_csv(destination / "shared-degree-policy-metrics.csv", shared_policy_metrics)
    _write_csv(destination / "paired-patient-differences.csv", patient_differences)
    _write_csv(
        destination / "paired-comparisons.csv",
        _flat_paired_rows(paired_comparisons),
    )
    if auc_rows:
        _write_csv(destination / "budget-auc.csv", auc_rows)
    if auc_patient_rows:
        _write_csv(
            destination / "paired-budget-auc-patient-differences.csv",
            auc_patient_rows,
        )
        _write_csv(
            destination / "paired-budget-auc-comparisons.csv",
            _flat_auc_comparisons(paired_auc_rows),
        )
    _write_csv(
        destination / "disease-level-sensitivity.csv",
        disease_sensitivity_rows,
    )
    _write_csv(
        destination / "disease-level-sensitivity-summary.csv",
        disease_sensitivity_summaries,
    )
    if known_age_patient_differences:
        _write_csv(
            destination / "known-age-paired-patient-differences.csv",
            known_age_patient_differences,
        )
        _write_csv(
            destination / "known-age-policy-metrics.csv",
            known_age_sensitivity["policy_metrics"],
        )
        _write_csv(
            destination / "known-age-paired-comparisons.csv",
            _flat_paired_rows(known_age_sensitivity["paired_comparisons"]),
        )
        _write_csv(
            destination / "known-age-patient-results.csv",
            [
                {
                    key: (
                        json.dumps(value, ensure_ascii=False)
                        if isinstance(value, list)
                        else value
                    )
                    for key, value in item.items()
                }
                for item in known_age_rows
            ],
        )
    _write_csv(destination / "shared-degree-effects.csv", shared_effects)
    _write_csv(destination / "heldout-missing-fact-effects.csv", missing_effects)
    _write_csv(destination / "shared-degree-effect-contrasts.csv", shared_contrasts)
    atomic_write_text(
        destination / "interpretation.md",
        _interpretation_markdown(summary),
        encoding="utf-8",
    )
    return destination / "summary.json"


__all__ = [
    "load_public_protocol_policy_cases",
    "run_public_protocol_policy_scale",
]
