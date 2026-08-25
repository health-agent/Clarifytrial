"""Batch evaluation of the same connected workflow used by the terminal app."""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from math import comb
from pathlib import Path
from statistics import mean
from typing import Any

from ..agents import (
    CoordinatorAgent,
    MatcherJudgeAgent,
    NextEvidenceAgent,
    SelectiveReviewerAgent,
)
from ..environment import (
    HiddenPatientEnvironment,
    PublicFactRequest,
    PublicQuestionCatalog,
    SyntheticInformationTools,
)
from ..contracts import TrialSearchRank
from ..llm import DeterministicWorkflowModel, StructuredModel
from ..io import atomic_write_text
from ..interactive.burden_contracts import PatientBurdenInput
from ..preparation import summarize_model_usage
from ..preparation.team_trials import TeamTrialCandidateSearch
from ..settings import EpisodeSettings
from ..trace import TraceRecorder
from ..ui import build_integrated_ui_fixture
from ..workflow import EpisodeAgents, PatientScreeningRunner


_ARMS = {
    "no_questions": {"actions": 0, "question_policy": "clarifytrial"},
    "fixed_order": {"actions": 3, "question_policy": "fixed_order"},
    "immediate_coverage": {
        "actions": 3,
        "question_policy": "immediate_coverage",
    },
    "clarifytrial": {"actions": 3, "question_policy": "clarifytrial"},
}
_AGENT_ARCHITECTURES = frozenset(
    {
        "rules_only",
        "single_judge",
        "code_routed_agents",
        "full_agents_no_reviewer",
        "full_agents",
    }
)


def _file_sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _row_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row["patient_id"]),
        str(row.get("scenario", "all_answers_available")),
        str(row["arm"]),
    )


def _load_case_rows(path: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Read the latest row per run key and tolerate one torn final line."""

    if not path.is_file():
        return {}
    lines = path.read_text(encoding="utf-8").splitlines()
    rows: dict[tuple[str, str, str], dict[str, Any]] = {}
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            if index == len(lines) - 1:
                continue
            raise ValueError(f"invalid workflow case JSON at line {index + 1}")
        rows[_row_key(row)] = row
    return rows


def _agents(
    model: StructuredModel,
    *,
    architecture: str,
) -> EpisodeAgents:
    deterministic = DeterministicWorkflowModel()
    if architecture == "rules_only":
        coordinator_model = deterministic
        matcher_model = deterministic
        next_evidence_model = deterministic
        reviewer_model = deterministic
    elif architecture == "single_judge":
        coordinator_model = deterministic
        matcher_model = model
        next_evidence_model = deterministic
        reviewer_model = deterministic
    else:
        coordinator_model = model
        matcher_model = model
        next_evidence_model = model
        reviewer_model = model
    return EpisodeAgents(
        coordinator=CoordinatorAgent(coordinator_model),
        matcher_judge=MatcherJudgeAgent(matcher_model),
        next_evidence=NextEvidenceAgent(next_evidence_model),
        selective_reviewer=SelectiveReviewerAgent(reviewer_model),
    )


def _tools(
    fixture: Any,
    *,
    unavailable_fact_ids: frozenset[str] = frozenset(),
) -> SyntheticInformationTools:
    return SyntheticInformationTools(
        PublicQuestionCatalog(
            [
                PublicFactRequest(
                    fact_id=item.fact_id,
                    description=item.description,
                    available_actions=tuple(item.acceptable_actions),
                )
                for item in fixture.screening_case.evidence_requests
            ]
        ),
        HiddenPatientEnvironment(
            item
            for item in fixture.hidden_answers
            if item.fact_id not in unavailable_fact_ids
        ),
    )


def _role_usage(trace: TraceRecorder) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for event in trace.events:
        if event.usage is None:
            continue
        row = result.setdefault(
            event.actor,
            {
                "call_count": 0,
                "external_call_count": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "total_tokens": 0,
            },
        )
        row["call_count"] += 1
        row["external_call_count"] += (
            event.usage.get("model_id") != "deterministic-workflow"
        )
        row["input_tokens"] += int(event.usage.get("input_tokens") or 0)
        row["output_tokens"] += int(event.usage.get("output_tokens") or 0)
        row["total_tokens"] += int(event.usage.get("total_tokens") or 0)
    return dict(sorted(result.items()))


def _aggregate_role_usage(items: Sequence[dict[str, Any]]) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = {}
    for item in items:
        for role, usage in item.get("role_usage", {}).items():
            row = result.setdefault(
                str(role),
                {
                    "call_count": 0,
                    "external_call_count": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
            )
            for name in row:
                row[name] += int(usage.get(name, 0))
    return dict(sorted(result.items()))


def _apply_broad_search(
    fixture: Any,
    searcher: TeamTrialCandidateSearch,
    *,
    top_k: int,
) -> tuple[Any, dict[str, Any]]:
    """Check target connectivity, then screen only predeclared target trials."""

    hits = searcher.search(fixture.search_conditions, top_k=top_k)
    rank_by_id = {item.source.trial_id: item for item in hits}
    expected = list(fixture.expected_candidate_trial_ids)
    recovered = [trial_id for trial_id in expected if trial_id in rank_by_id]
    if not recovered:
        raise ValueError("broad candidate search recovered none of the target trials")
    recovered_set = set(recovered)
    screening_trials = [
        trial
        for trial in fixture.screening_case.trials
        if trial.trial_id in recovered_set
    ]
    known_criteria = {
        criterion.criterion_id
        for trial in screening_trials
        for criterion in trial.criteria
    }
    requests = []
    for request in fixture.screening_case.evidence_requests:
        related = [
            criterion_id
            for criterion_id in request.related_criterion_ids
            if criterion_id in known_criteria
        ]
        if related:
            requests.append(
                request.model_copy(update={"related_criterion_ids": related})
            )
    fact_ids = {item.fact_id for item in requests}
    ranking = [
        TrialSearchRank(
            trial_id=trial_id,
            rank=rank_by_id[trial_id].rank,
            score=rank_by_id[trial_id].score,
            retrieval_method=rank_by_id[trial_id].retrieval_method,
        )
        for trial_id in recovered
    ]
    screening_case = fixture.screening_case.model_copy(
        update={
            "trials": screening_trials,
            "evidence_requests": requests,
            "acquisition_options": [
                item
                for item in fixture.screening_case.acquisition_options
                if item.fact_id in fact_ids
            ],
            "candidate_ranking": ranking,
        }
    )
    search_result = {
        "corpus_trial_count": searcher.summary.included_trial_count,
        "top_k": top_k,
        "retrieved_candidate_count": len(hits),
        "target_trial_count": len(expected),
        "retrieved_target_count": len(recovered),
        "target_recall": len(recovered) / len(expected),
        "retrieved_target_trial_ids": recovered,
        "missed_target_trial_ids": sorted(set(expected) - recovered_set),
        "target_ranks": {
            trial_id: rank_by_id[trial_id].rank for trial_id in recovered
        },
        "retrieval_method": searcher.retrieval_method,
        "screens_retrieved_distractors": False,
    }
    return (
        replace(
            fixture,
            screening_case=screening_case,
            candidate_hits=tuple(hits),
            hidden_answers=tuple(
                item for item in fixture.hidden_answers if item.fact_id in fact_ids
            ),
        ),
        search_result,
    )


def _decision_map(rows: Sequence[dict[str, Any]]) -> dict[str, tuple[str, str]]:
    return {
        str(item["trial_id"]): (
            str(item["candidate_status"]),
            str(item["confirmation_status"]),
        )
        for item in rows
    }


def _metrics(
    *,
    final_rows: Sequence[dict[str, Any]],
    initial_rows: Sequence[dict[str, Any]],
    gold_rows: Sequence[dict[str, Any]],
    initial_gold_rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    final = _decision_map(final_rows)
    initial = _decision_map(initial_rows)
    gold = _decision_map(gold_rows)
    initial_gold = _decision_map(initial_gold_rows)
    trial_ids = sorted(gold)
    exact = sum(final.get(item) == gold[item] for item in trial_ids)
    candidate = sum(
        final.get(item, (None, None))[0] == gold[item][0] for item in trial_ids
    )
    confirmation = sum(
        final.get(item, (None, None))[1] == gold[item][1] for item in trial_ids
    )
    resolved_states = {"confirmed", "ineligible"}
    rescue_opportunities = {
        item
        for item in trial_ids
        if gold[item] == ("retain", "confirmed")
        and initial.get(item) != ("retain", "confirmed")
    }
    false_preservations = {
        item
        for item in trial_ids
        if initial.get(item) == ("retain", "not_confirmed")
        and gold[item] == ("remove", "ineligible")
    }
    return {
        "trial_count": len(trial_ids),
        "exact_trial_status_count": exact,
        "trial_status_recovery": exact / len(trial_ids),
        "candidate_status_accuracy": candidate / len(trial_ids),
        "confirmation_status_accuracy": confirmation / len(trial_ids),
        "false_candidate_removals": sum(
            (
                item not in final
                or final.get(item, (None, None))[0] == "remove"
            )
            and gold[item][0] == "retain"
            for item in trial_ids
        ),
        "premature_initial_confirmations": sum(
            initial.get(item, (None, None))[1] == "confirmed"
            and initial_gold.get(item, (None, None))[1] != "confirmed"
            for item in trial_ids
        ),
        "premature_final_confirmations": sum(
            final.get(item, (None, None))[1] == "confirmed"
            and gold[item][1] != "confirmed"
            for item in trial_ids
        ),
        "unresolved_to_resolved": sum(
            initial.get(item, (None, "uncertain"))[1] not in resolved_states
            and final.get(item, (None, "uncertain"))[1] in resolved_states
            for item in trial_ids
        ),
        "resolved_to_unresolved": sum(
            initial.get(item, (None, "uncertain"))[1] in resolved_states
            and final.get(item, (None, "uncertain"))[1] not in resolved_states
            for item in trial_ids
        ),
        "rescue_opportunity_count": len(rescue_opportunities),
        "candidate_preservation_count": sum(
            initial.get(item, (None, None))[0] == "retain"
            for item in rescue_opportunities
        ),
        "confirmed_rescue_count": sum(
            final.get(item) == ("retain", "confirmed")
            for item in rescue_opportunities
        ),
        "false_preservation_count": len(false_preservations),
        "false_preservation_resolved_count": sum(
            final.get(item) == ("remove", "ineligible")
            for item in false_preservations
        ),
    }


def _percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires at least one value")
    position = (len(ordered) - 1) * probability
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _cluster_rate_summary(
    items: Sequence[dict[str, Any]],
    *,
    numerator: Callable[[dict[str, Any]], int],
    denominator: Callable[[dict[str, Any]], int],
    seed: int = 20260825,
    resamples: int = 2_000,
) -> dict[str, Any] | None:
    eligible = [item for item in items if denominator(item) > 0]
    if not eligible:
        return None
    generator = random.Random(seed)
    samples = []
    for _ in range(resamples):
        drawn = [generator.choice(eligible) for _ in eligible]
        total_denominator = sum(denominator(item) for item in drawn)
        samples.append(
            sum(numerator(item) for item in drawn) / total_denominator
        )

    group_rates = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in eligible:
        grouped[str(item.get("group_id", "unspecified"))].append(item)
    for group_items in grouped.values():
        group_denominator = sum(denominator(item) for item in group_items)
        if group_denominator:
            group_rates.append(
                sum(numerator(item) for item in group_items) / group_denominator
            )
    return {
        "cluster_unit": "patient",
        "bootstrap_resamples": resamples,
        "bootstrap_seed": seed,
        "bootstrap_95_ci": {
            "lower": _percentile(samples, 0.025),
            "upper": _percentile(samples, 0.975),
        },
        "disease_group_count": len(group_rates),
        "disease_group_rate_range": (
            {"minimum": min(group_rates), "maximum": max(group_rates)}
            if group_rates
            else None
        ),
    }


def _aggregate(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in rows:
        if item.get("status") == "completed":
            grouped[item["arm"]].append(item)
    result = []
    for arm in _ARMS:
        items = grouped.get(arm, [])
        if not items:
            continue
        trial_count = sum(item["metrics"]["trial_count"] for item in items)
        rescue_opportunities = sum(
            item["metrics"]["rescue_opportunity_count"] for item in items
        )
        preserved = sum(
            item["metrics"]["candidate_preservation_count"] for item in items
        )
        rescued = sum(
            item["metrics"]["confirmed_rescue_count"] for item in items
        )
        false_preservations = sum(
            item["metrics"]["false_preservation_count"] for item in items
        )
        false_resolved = sum(
            item["metrics"]["false_preservation_resolved_count"]
            for item in items
        )
        arm_result = {
                "arm": arm,
                "patient_count": len(items),
                "trial_count": trial_count,
                "trial_status_recovery": sum(
                    item["metrics"]["exact_trial_status_count"] for item in items
                )
                / trial_count,
                "candidate_status_accuracy": sum(
                    item["metrics"]["candidate_status_accuracy"]
                    * item["metrics"]["trial_count"]
                    for item in items
                )
                / trial_count,
                "confirmation_status_accuracy": sum(
                    item["metrics"]["confirmation_status_accuracy"]
                    * item["metrics"]["trial_count"]
                    for item in items
                )
                / trial_count,
                "mean_action_count": mean(item["action_count"] for item in items),
                "unavailable_action_count": sum(
                    item["unavailable_action_count"] for item in items
                ),
                "repeated_fact_action_count": sum(
                    item["repeated_fact_action_count"] for item in items
                ),
                "mean_unresolved_to_resolved": mean(
                    item["metrics"]["unresolved_to_resolved"] for item in items
                ),
                "false_candidate_removals": sum(
                    item["metrics"]["false_candidate_removals"] for item in items
                ),
                "premature_initial_confirmations": sum(
                    item["metrics"]["premature_initial_confirmations"]
                    for item in items
                ),
                "premature_final_confirmations": sum(
                    item["metrics"]["premature_final_confirmations"]
                    for item in items
                ),
                "resolved_to_unresolved": sum(
                    item["metrics"]["resolved_to_unresolved"] for item in items
                ),
                "rescue_opportunity_count": rescue_opportunities,
                "candidate_preservation_count": preserved,
                "candidate_preservation_rate": (
                    preserved / rescue_opportunities
                    if rescue_opportunities
                    else None
                ),
                "confirmed_rescue_count": rescued,
                "confirmed_rescue_rate": (
                    rescued / rescue_opportunities
                    if rescue_opportunities
                    else None
                ),
                "false_preservation_count": false_preservations,
                "false_preservation_resolved_count": false_resolved,
                "false_preservation_resolution_rate": (
                    false_resolved / false_preservations
                    if false_preservations
                    else None
                ),
                "new_test_count": sum(item.get("new_test_count", 0) for item in items),
                "additional_visit_count": sum(
                    item.get("additional_visit_count", 0) for item in items
                ),
                "patient_choice_action_count": sum(
                    item.get("patient_choice_action_count", 0) for item in items
                ),
                "selective_review_count": sum(
                    item.get("review_count", 0) for item in items
                ),
                "mechanical_model_correction_count": sum(
                    item.get("mechanical_model_correction_count", 0)
                    for item in items
                ),
                "model_call_count": sum(item["usage"]["call_count"] for item in items),
                "external_model_call_count": sum(
                    sum(
                        int(usage.get("external_call_count", 0))
                        for usage in item.get("role_usage", {}).values()
                    )
                    for item in items
                ),
                "total_tokens": sum(item["usage"]["total_tokens"] for item in items),
                "total_latency_ms": sum(item["total_latency_ms"] for item in items),
                "role_usage": _aggregate_role_usage(items),
                "failed_patient_count": 0,
                "cluster_uncertainty": {
                    "trial_status_recovery": _cluster_rate_summary(
                        items,
                        numerator=lambda item: item["metrics"][
                            "exact_trial_status_count"
                        ],
                        denominator=lambda item: item["metrics"]["trial_count"],
                    ),
                    "confirmed_rescue_rate": _cluster_rate_summary(
                        items,
                        numerator=lambda item: item["metrics"][
                            "confirmed_rescue_count"
                        ],
                        denominator=lambda item: item["metrics"][
                            "rescue_opportunity_count"
                        ],
                    ),
                    "false_preservation_resolution_rate": _cluster_rate_summary(
                        items,
                        numerator=lambda item: item["metrics"][
                            "false_preservation_resolved_count"
                        ],
                        denominator=lambda item: item["metrics"][
                            "false_preservation_count"
                        ],
                    ),
                },
            }
        result.append(arm_result)
    for arm in _ARMS:
        failures = sum(
            item["arm"] == arm and item.get("status") == "failed" for item in rows
        )
        for item in result:
            if item["arm"] == arm:
                item["failed_patient_count"] = failures
    return result


def _aggregate_by_group(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep disease-level variation visible instead of reporting one mean only."""

    groups = sorted({str(item["group_id"]) for item in rows})
    result = []
    for group_id in groups:
        group_rows = [
            item for item in rows if str(item["group_id"]) == group_id
        ]
        group_label = next(
            (
                str(item["group_label"])
                for item in group_rows
                if item.get("group_label")
            ),
            group_id,
        )
        for arm_metrics in _aggregate(group_rows):
            result.append(
                {
                    "group_id": group_id,
                    "group_label": group_label,
                    **arm_metrics,
                }
            )
    return result


def _aggregate_broad_search(rows: Sequence[dict[str, Any]]) -> dict[str, Any] | None:
    by_patient: dict[str, dict[str, Any]] = {}
    for row in rows:
        search = row.get("broad_search")
        if search is not None and row.get("status") == "completed":
            by_patient.setdefault(str(row["patient_id"]), search)
    if not by_patient:
        return None
    values = list(by_patient.values())
    target_count = sum(int(item["target_trial_count"]) for item in values)
    retrieved_count = sum(int(item["retrieved_target_count"]) for item in values)
    unique_target_ids = {
        str(trial_id)
        for item in values
        for trial_id in (
            list(item.get("retrieved_target_trial_ids", []))
            + list(item.get("missed_target_trial_ids", []))
        )
    }
    unique_retrieved_ids = {
        str(trial_id)
        for item in values
        for trial_id in item.get("retrieved_target_trial_ids", [])
    }
    ranks = [
        int(rank)
        for item in values
        for rank in item["target_ranks"].values()
    ]
    return {
        "patient_count": len(values),
        "corpus_trial_count": values[0]["corpus_trial_count"],
        "top_k": values[0]["top_k"],
        "target_patient_trial_count": target_count,
        "retrieved_target_patient_trial_count": retrieved_count,
        "target_trial_count": target_count,
        "retrieved_target_count": retrieved_count,
        "target_recall": retrieved_count / target_count,
        "unique_target_trial_count": len(unique_target_ids),
        "unique_retrieved_target_trial_count": len(unique_retrieved_ids),
        "unique_target_recall": (
            len(unique_retrieved_ids) / len(unique_target_ids)
            if unique_target_ids
            else None
        ),
        "mean_retrieved_target_rank": mean(ranks) if ranks else None,
        "worst_retrieved_target_rank": max(ranks) if ranks else None,
        "missed_target_trial_count": target_count - retrieved_count,
        "retrieval_method": values[0]["retrieval_method"],
        "screens_retrieved_distractors": False,
    }


def _paired(
    rows: Sequence[dict[str, Any]],
    *,
    baseline_arm: str,
) -> dict[str, Any]:
    completed = {
        (item["patient_id"], item["arm"]): item
        for item in rows
        if item.get("status") == "completed"
    }
    patient_ids = sorted(
        patient_id
        for patient_id, arm in completed
        if arm == "clarifytrial" and (patient_id, baseline_arm) in completed
    )
    differences = [
        completed[(item, "clarifytrial")]["metrics"]["trial_status_recovery"]
        - completed[(item, baseline_arm)]["metrics"]["trial_status_recovery"]
        for item in patient_ids
    ]
    wins = sum(item > 1e-12 for item in differences)
    losses = sum(item < -1e-12 for item in differences)
    ties = len(differences) - wins - losses
    non_ties = wins + losses
    smaller = min(wins, losses)
    sign_p = (
        min(
            1.0,
            2 * sum(comb(non_ties, index) for index in range(smaller + 1))
            / (2**non_ties),
        )
        if non_ties
        else 1.0
    )
    return {
        "baseline_arm": baseline_arm,
        "patient_count": len(patient_ids),
        "mean_recovery_difference": mean(differences) if differences else None,
        "clarifytrial_better_patient_count": wins,
        "equal_patient_count": ties,
        "clarifytrial_worse_patient_count": losses,
        "two_sided_exact_sign_test_p": sign_p,
    }


def _decision_separation_summary(pairs: Sequence[dict[str, Any]]) -> dict[str, Any]:
    """Count states that one binary answer cannot represent without loss."""

    decisions = [
        decision
        for pair in pairs
        for decision in pair["insufficient_evidence_episode"][
            "expected_trial_decisions"
        ]
    ]
    retained_but_not_confirmed = sum(
        item["candidate_status"] == "retain"
        and item["confirmation_status"] == "not_confirmed"
        for item in decisions
    )
    return {
        "trial_decision_count": len(decisions),
        "retained_but_not_confirmed_count": retained_but_not_confirmed,
        "if_only_confirmed_trials_are_kept_false_removals": (
            retained_but_not_confirmed
        ),
        "if_every_retained_trial_is_called_confirmed_premature_confirmations": (
            retained_but_not_confirmed
        ),
    }


def _least_connected_fact_id(fixture: Any) -> str:
    criterion_to_trial = {
        criterion.criterion_id: trial.trial_id
        for trial in fixture.screening_case.trials
        for criterion in trial.criteria
    }
    request = min(
        fixture.screening_case.evidence_requests,
        key=lambda item: (
            len(
                {
                    criterion_to_trial[criterion_id]
                    for criterion_id in item.related_criterion_ids
                }
            ),
            item.fact_id,
        ),
    )
    return request.fact_id


def run_full_workflow_evaluation(
    *,
    trial_set_path: str | Path,
    patient_pairs_path: str | Path,
    generation_config_path: str | Path,
    destination: str | Path,
    model: StructuredModel,
    model_label: str,
    split: str = "heldout",
    patient_ids: Sequence[str] = (),
    limit: int | None = None,
    action_budget: int = 3,
    arms: Sequence[str] = tuple(_ARMS),
    max_selective_reviews: int = 1,
    max_cycles: int = 12,
    agent_architecture: str = "rules_only",
    concurrency: int = 1,
    include_unavailable_scenario: bool = False,
    include_patient_choice_scenario: bool = False,
    approve_synthetic_actions: bool = False,
    broad_corpus_path: str | Path | None = None,
    broad_search_top_k: int = 200,
    resume: bool = False,
    progress: Any = print,
) -> dict[str, Any]:
    if concurrency < 1:
        raise ValueError("concurrency must be at least one")
    if broad_search_top_k < 1:
        raise ValueError("broad_search_top_k must be at least one")
    if agent_architecture not in _AGENT_ARCHITECTURES:
        raise ValueError(
            "agent_architecture must be one of: "
            + ", ".join(sorted(_AGENT_ARCHITECTURES))
        )
    selected_arms = tuple(dict.fromkeys(str(arm) for arm in arms))
    unknown_arms = set(selected_arms) - set(_ARMS)
    if not selected_arms or unknown_arms:
        raise ValueError(
            "arms must contain one or more of: " + ", ".join(_ARMS)
        )
    pairs_document = json.loads(Path(patient_pairs_path).read_text(encoding="utf-8"))
    group_label_by_id = {
        str(item["group_id"]): str(item["group_label"])
        for item in pairs_document.get("groups", [])
        if item.get("group_label")
    }
    requested = set(patient_ids)
    pairs = [
        item
        for item in pairs_document["pairs"]
        if str(item["split"]) == split
        and (not requested or str(item["patient_id"]) in requested)
    ]
    if limit is not None:
        pairs = pairs[:limit]
    if not pairs:
        raise ValueError("workflow evaluation selected no patients")
    output_dir = Path(destination)
    output_dir.mkdir(parents=True, exist_ok=True)
    case_path = output_dir / "cases.jsonl"
    manifest_path = output_dir / "run-manifest.json"
    manifest = {
        "protocol_id": "clarifytrial-full-workflow-evaluation-v4",
        "model": (
            "deterministic-workflow"
            if agent_architecture == "rules_only"
            else model_label
        ),
        "agent_architecture": agent_architecture,
        "split": split,
        "patient_ids": [str(item["patient_id"]) for item in pairs],
        "action_budget": action_budget,
        "max_selective_reviews": max_selective_reviews,
        "max_cycles": max_cycles,
        "include_unavailable_scenario": include_unavailable_scenario,
        "include_patient_choice_scenario": include_patient_choice_scenario,
        "approve_synthetic_actions": approve_synthetic_actions,
        "broad_search_top_k": (
            broad_search_top_k if broad_corpus_path is not None else None
        ),
        "unavailable_answer_selection": (
            "fewest_connected_trials_then_fact_id"
            if include_unavailable_scenario
            else None
        ),
        "arms": list(selected_arms),
        "inputs": {
            "trial_set_sha256": _file_sha256(trial_set_path),
            "patient_pairs_sha256": _file_sha256(patient_pairs_path),
            "generation_config_sha256": _file_sha256(generation_config_path),
            "broad_corpus_sha256": (
                None
                if broad_corpus_path is None
                else _file_sha256(broad_corpus_path)
            ),
        },
    }
    if resume:
        if not manifest_path.is_file() or not case_path.is_file():
            raise ValueError("resume requires run-manifest.json and cases.jsonl")
        existing_manifest = json.loads(
            manifest_path.read_text(encoding="utf-8")
        )
        if existing_manifest != manifest:
            raise ValueError(
                "resume settings or input files differ from the saved run"
            )
        row_by_key = _load_case_rows(case_path)
    else:
        atomic_write_text(
            manifest_path,
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        )
        row_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    completed_keys = {
        key for key, row in row_by_key.items() if row.get("status") == "completed"
    }
    broad_searcher = (
        None
        if broad_corpus_path is None
        else TeamTrialCandidateSearch(broad_corpus_path)
    )

    def evaluate_pair(pair: dict[str, Any]) -> list[dict[str, Any]]:
        patient_rows = []
        patient_id = str(pair["patient_id"])
        fixture = build_integrated_ui_fixture(
            trial_set_path=trial_set_path,
            patient_pairs_path=patient_pairs_path,
            generation_config_path=generation_config_path,
            patient_id=patient_id,
        )
        broad_search_result = None
        if broad_searcher is not None:
            fixture, broad_search_result = _apply_broad_search(
                fixture,
                broad_searcher,
                top_k=broad_search_top_k,
            )
        full_gold = pair["sufficient_evidence_episode"]["expected_trial_decisions"]
        initial_gold = pair["insufficient_evidence_episode"][
            "expected_trial_decisions"
        ]
        scenarios: list[
            tuple[str, frozenset[str], PatientBurdenInput | None]
        ] = [("all_answers_available", frozenset(), None)]
        if include_unavailable_scenario:
            unavailable_fact_id = _least_connected_fact_id(fixture)
            scenarios.append(
                (
                    "one_answer_unavailable",
                    frozenset({unavailable_fact_id}),
                    None,
                )
            )
        if include_patient_choice_scenario:
            scenarios.append(
                (
                    "patient_declines_new_tests",
                    frozenset(),
                    PatientBurdenInput.model_validate(
                        {
                            "preference_mode": "least_extra_burden",
                            "stated_limits": {
                                "max_additional_visits": 0,
                                "allow_new_tests": False,
                                "allow_treatment_change": False,
                            },
                        }
                    ),
                )
            )
        for scenario, unavailable_fact_ids, burden_input in scenarios:
            for arm in selected_arms:
                arm_spec = _ARMS[arm]
                key = (patient_id, scenario, arm)
                if key in completed_keys:
                    continue
                trace = TraceRecorder(f"{patient_id}:{scenario}:{arm}")
                settings = EpisodeSettings(
                    max_external_actions=(0 if arm == "no_questions" else action_budget),
                    max_selective_reviews=(
                        0
                        if agent_architecture
                        in {
                            "rules_only",
                            "single_judge",
                            "full_agents_no_reviewer",
                        }
                        else max_selective_reviews
                    ),
                    max_cycles=max_cycles,
                    use_model_coordinator=agent_architecture
                    in {"full_agents_no_reviewer", "full_agents"},
                    batch_trial_judgments=True,
                    question_policy=arm_spec["question_policy"],
                )
                try:
                    screening_case = fixture.screening_case.model_copy(
                        update={"patient_burden_input": burden_input}
                    )
                    result = PatientScreeningRunner(
                        _agents(model, architecture=agent_architecture), settings
                    ).run(
                        screening_case,
                        _tools(
                            fixture,
                            unavailable_fact_ids=unavailable_fact_ids,
                        ),
                        trace=trace,
                        patient_approved_option_ids=(
                            {
                                option.option_id
                                for option in fixture.screening_case.acquisition_options
                                if option.requires_patient_choice
                            }
                            if approve_synthetic_actions
                            else None
                        ),
                        clinician_authorized_option_ids=(
                            {
                                option.option_id
                                for option in fixture.screening_case.acquisition_options
                                if option.requires_clinician_authorization
                            }
                            if approve_synthetic_actions
                            else None
                        ),
                    )
                except Exception as error:
                    row = {
                        "patient_id": patient_id,
                        "group_id": str(pair["group_id"]),
                        "group_label": group_label_by_id.get(
                            str(pair["group_id"]), str(pair["group_id"])
                        ),
                        "split": split,
                        "scenario": scenario,
                        "unavailable_fact_ids": sorted(unavailable_fact_ids),
                        "patient_choice_scenario": scenario,
                        "arm": arm,
                        "agent_architecture": agent_architecture,
                        "broad_search": broad_search_result,
                        "status": "failed",
                        "error_type": type(error).__name__,
                        "error": str(error),
                    }
                else:
                    dumped = result.model_dump(mode="json")
                    initial_rows = result.decision_history[0].model_dump(mode="json")[
                        "decisions"
                    ]
                    usage = summarize_model_usage(trace).model_dump(mode="json")
                    total_latency = sum(
                        int(event.usage.get("latency_ms") or 0)
                        for event in trace.events
                        if event.usage is not None
                    )
                    selected_fact_ids = [
                        item.agent_action.target_fact_id
                        for item in result.action_history
                    ]
                    selected_options = [
                        item.acquisition_decision.selected_option
                        for item in result.action_history
                        if item.acquisition_decision.selected_option is not None
                    ]
                    row = {
                        "patient_id": patient_id,
                        "group_id": str(pair["group_id"]),
                        "group_label": group_label_by_id.get(
                            str(pair["group_id"]), str(pair["group_id"])
                        ),
                        "split": split,
                        "scenario": scenario,
                        "unavailable_fact_ids": sorted(unavailable_fact_ids),
                        "arm": arm,
                        "agent_architecture": agent_architecture,
                        "broad_search": broad_search_result,
                        "status": "completed",
                        "stop_reason": result.stop_reason.value,
                        "action_count": len(result.action_history),
                        "review_count": len(result.review_history),
                        "mechanical_model_correction_count": sum(
                            len(event.output.get("corrections", []))
                            for event in trace.events
                            if event.actor == "mechanical_checks"
                            and event.event == "model_assessments_replaced"
                        ),
                        "review_history": dumped["review_history"],
                        "initial_review_reasons": {
                            item["trial_id"]: item["review_reasons"]
                            for item in initial_rows
                            if item["review_reasons"]
                        },
                        "initial_review_flags": {
                            item["trial_id"]: {
                                assessment["criterion_id"]: assessment[
                                    "review_flags"
                                ]
                                for assessment in item["criterion_assessments"]
                                if assessment["review_flags"]
                            }
                            for item in initial_rows
                            if any(
                                assessment["review_flags"]
                                for assessment in item["criterion_assessments"]
                            )
                        },
                        "selected_fact_ids": selected_fact_ids,
                        "selected_option_ids": [
                            item.option_id for item in selected_options
                        ],
                        "new_test_count": sum(
                            item.new_test_required for item in selected_options
                        ),
                        "additional_visit_count": sum(
                            item.visit_required is True for item in selected_options
                        ),
                        "patient_choice_action_count": sum(
                            item.requires_patient_choice for item in selected_options
                        ),
                        "unavailable_action_count": sum(
                            item.tool_result.status.value == "not_available"
                            for item in result.action_history
                        ),
                        "repeated_fact_action_count": (
                            len(selected_fact_ids) - len(set(selected_fact_ids))
                        ),
                        "final_decisions": [
                            {
                                "trial_id": item["trial_id"],
                                "candidate_status": item["candidate_status"],
                                "confirmation_status": item["confirmation_status"],
                                "pending_fact_ids": [
                                    request["fact_id"]
                                    for request in item["pending_information"]
                                ],
                            }
                            for item in dumped["final_decisions"]
                        ],
                        "metrics": _metrics(
                            final_rows=dumped["final_decisions"],
                            initial_rows=initial_rows,
                            gold_rows=full_gold,
                            initial_gold_rows=initial_gold,
                        ),
                        "usage": usage,
                        "role_usage": _role_usage(trace),
                        "total_latency_ms": total_latency,
                    }
                patient_rows.append(row)
        return patient_rows

    with case_path.open(
        "a" if resume else "w",
        encoding="utf-8",
        newline="\n",
    ) as stream:
        if concurrency == 1:
            completed_batches = (evaluate_pair(pair) for pair in pairs)
            for patient_index, patient_rows in enumerate(completed_batches, start=1):
                for row in patient_rows:
                    row_by_key[_row_key(row)] = row
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                progress(
                    f"processed {patient_index}/{len(pairs)} patients "
                    f"({len(patient_rows)} new runs)"
                )
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = {executor.submit(evaluate_pair, pair): pair for pair in pairs}
                for patient_index, future in enumerate(as_completed(futures), start=1):
                    patient_rows = future.result()
                    for row in patient_rows:
                        row_by_key[_row_key(row)] = row
                        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                    stream.flush()
                    progress(
                        f"processed {patient_index}/{len(pairs)} patients "
                        f"({len(patient_rows)} new runs)"
                    )

    rows = list(row_by_key.values())
    normal_rows = [
        item for item in rows if item.get("scenario") == "all_answers_available"
    ]
    unavailable_rows = [
        item for item in rows if item.get("scenario") == "one_answer_unavailable"
    ]
    patient_choice_rows = [
        item
        for item in rows
        if item.get("scenario") == "patient_declines_new_tests"
    ]
    payload = {
        "protocol_id": "clarifytrial-full-workflow-evaluation-v4",
        "model": (
            "deterministic-workflow"
            if agent_architecture == "rules_only"
            else model_label
        ),
        "agent_architecture": agent_architecture,
        "split": split,
        "patient_count": len(pairs),
        "arms": list(selected_arms),
        "action_budget": action_budget,
        "concurrency": concurrency,
        "include_unavailable_scenario": include_unavailable_scenario,
        "include_patient_choice_scenario": include_patient_choice_scenario,
        "approve_synthetic_actions": approve_synthetic_actions,
        "broad_search_top_k": (
            broad_search_top_k if broad_searcher is not None else None
        ),
        "unavailable_answer_selection": (
            "fewest_connected_trials_then_fact_id"
            if include_unavailable_scenario
            else None
        ),
        "resumed": resume,
        "case_results": "cases.jsonl",
        "evaluation_scope": {
            "patient_input": "standardized_json",
            "candidate_selection": (
                "broad_corpus_target_connectivity_then_declared_target_evaluation"
                if broad_searcher is not None
                else "five_declared_same_disease_trials"
            ),
            "includes_broad_corpus_search": broad_searcher is not None,
            "screens_all_retrieved_candidates": False,
            "retains_search_distractors": False,
            "includes_natural_record_structuring": False,
        },
        "broad_search_metrics": _aggregate_broad_search(normal_rows),
        "arm_metrics": _aggregate(normal_rows),
        "group_metrics": _aggregate_by_group(normal_rows),
        "unavailable_answer_metrics": (
            _aggregate(unavailable_rows) if unavailable_rows else []
        ),
        "patient_declines_new_tests_metrics": (
            _aggregate(patient_choice_rows) if patient_choice_rows else []
        ),
        "decision_separation": _decision_separation_summary(pairs),
        "paired_clarifytrial_vs_fixed": (
            _paired(normal_rows, baseline_arm="fixed_order")
            if {"clarifytrial", "fixed_order"}.issubset(selected_arms)
            else None
        ),
        "paired_clarifytrial_vs_immediate_coverage": (
            _paired(normal_rows, baseline_arm="immediate_coverage")
            if {"clarifytrial", "immediate_coverage"}.issubset(selected_arms)
            else None
        ),
    }
    summary_path = output_dir / "summary.json"
    atomic_write_text(
        summary_path,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


__all__ = ["run_full_workflow_evaluation"]
