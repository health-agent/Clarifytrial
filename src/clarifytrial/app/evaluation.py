"""Batch evaluation of the same connected workflow used by the terminal app."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
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
from ..llm import StructuredModel
from ..preparation import summarize_model_usage
from ..settings import EpisodeSettings
from ..trace import TraceRecorder
from ..ui import build_integrated_ui_fixture
from ..workflow import EpisodeAgents, PatientScreeningRunner


_ARMS = {
    "no_questions": {"actions": 0, "question_policy": "clarifytrial"},
    "fixed_order": {"actions": 3, "question_policy": "fixed_order"},
    "clarifytrial": {"actions": 3, "question_policy": "clarifytrial"},
}


def _agents(model: StructuredModel) -> EpisodeAgents:
    return EpisodeAgents(
        coordinator=CoordinatorAgent(model),
        matcher_judge=MatcherJudgeAgent(model),
        next_evidence=NextEvidenceAgent(model),
        selective_reviewer=SelectiveReviewerAgent(model),
    )


def _tools(fixture: Any) -> SyntheticInformationTools:
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
        HiddenPatientEnvironment(fixture.hidden_answers),
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
    resolved_before = sum(
        initial.get(item, (None, "uncertain"))[1] in {"confirmed", "ineligible"}
        for item in trial_ids
    )
    resolved_after = sum(
        final.get(item, (None, "uncertain"))[1] in {"confirmed", "ineligible"}
        for item in trial_ids
    )
    return {
        "trial_count": len(trial_ids),
        "exact_trial_status_count": exact,
        "trial_status_recovery": exact / len(trial_ids),
        "candidate_status_accuracy": candidate / len(trial_ids),
        "confirmation_status_accuracy": confirmation / len(trial_ids),
        "false_candidate_removals": sum(
            final.get(item, (None, None))[0] == "remove" and gold[item][0] == "retain"
            for item in trial_ids
        ),
        "premature_initial_confirmations": sum(
            initial.get(item, (None, None))[1] == "confirmed"
            and initial_gold.get(item, (None, None))[1] != "confirmed"
            for item in trial_ids
        ),
        "unresolved_to_resolved": max(0, resolved_after - resolved_before),
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
        result.append(
            {
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
                "model_call_count": sum(item["usage"]["call_count"] for item in items),
                "total_tokens": sum(item["usage"]["total_tokens"] for item in items),
                "total_latency_ms": sum(item["total_latency_ms"] for item in items),
                "failed_patient_count": 0,
            }
        )
    for arm in _ARMS:
        failures = sum(
            item["arm"] == arm and item.get("status") == "failed" for item in rows
        )
        for item in result:
            if item["arm"] == arm:
                item["failed_patient_count"] = failures
    return result


def _paired(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    completed = {
        (item["patient_id"], item["arm"]): item
        for item in rows
        if item.get("status") == "completed"
    }
    patient_ids = sorted(
        patient_id
        for patient_id, arm in completed
        if arm == "clarifytrial" and (patient_id, "fixed_order") in completed
    )
    differences = [
        completed[(item, "clarifytrial")]["metrics"]["trial_status_recovery"]
        - completed[(item, "fixed_order")]["metrics"]["trial_status_recovery"]
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
        "patient_count": len(patient_ids),
        "mean_recovery_difference": mean(differences) if differences else None,
        "clarifytrial_better_patient_count": wins,
        "equal_patient_count": ties,
        "clarifytrial_worse_patient_count": losses,
        "two_sided_exact_sign_test_p": sign_p,
    }


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
    max_selective_reviews: int = 1,
    max_cycles: int = 12,
    concurrency: int = 1,
    progress: Any = print,
) -> dict[str, Any]:
    if concurrency < 1:
        raise ValueError("concurrency must be at least one")
    pairs_document = json.loads(Path(patient_pairs_path).read_text(encoding="utf-8"))
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
    rows: list[dict[str, Any]] = []

    def evaluate_pair(pair: dict[str, Any]) -> list[dict[str, Any]]:
        patient_rows = []
        patient_id = str(pair["patient_id"])
        fixture = build_integrated_ui_fixture(
            trial_set_path=trial_set_path,
            patient_pairs_path=patient_pairs_path,
            generation_config_path=generation_config_path,
            patient_id=patient_id,
        )
        full_gold = pair["sufficient_evidence_episode"]["expected_trial_decisions"]
        initial_gold = pair["insufficient_evidence_episode"][
            "expected_trial_decisions"
        ]
        for arm, arm_spec in _ARMS.items():
            trace = TraceRecorder(f"{patient_id}:{arm}")
            settings = EpisodeSettings(
                max_external_actions=(0 if arm == "no_questions" else action_budget),
                max_selective_reviews=max_selective_reviews,
                max_cycles=max_cycles,
                use_model_coordinator=False,
                batch_trial_judgments=True,
                question_policy=arm_spec["question_policy"],
            )
            try:
                result = PatientScreeningRunner(_agents(model), settings).run(
                    fixture.screening_case,
                    _tools(fixture),
                    trace=trace,
                )
            except Exception as error:
                row = {
                    "patient_id": patient_id,
                    "split": split,
                    "arm": arm,
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
                row = {
                    "patient_id": patient_id,
                    "split": split,
                    "arm": arm,
                    "status": "completed",
                    "stop_reason": result.stop_reason.value,
                    "action_count": len(result.action_history),
                    "selected_fact_ids": [
                        item.agent_action.target_fact_id
                        for item in result.action_history
                    ],
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
                    "total_latency_ms": total_latency,
                }
            patient_rows.append(row)
        return patient_rows

    with case_path.open("w", encoding="utf-8", newline="\n") as stream:
        if concurrency == 1:
            completed_batches = (evaluate_pair(pair) for pair in pairs)
            for patient_index, patient_rows in enumerate(completed_batches, start=1):
                rows.extend(patient_rows)
                for row in patient_rows:
                    stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                stream.flush()
                progress(f"completed {patient_index}/{len(pairs)} patients")
        else:
            with ThreadPoolExecutor(max_workers=concurrency) as executor:
                futures = {executor.submit(evaluate_pair, pair): pair for pair in pairs}
                for patient_index, future in enumerate(as_completed(futures), start=1):
                    patient_rows = future.result()
                    rows.extend(patient_rows)
                    for row in patient_rows:
                        stream.write(json.dumps(row, ensure_ascii=False) + "\n")
                    stream.flush()
                    progress(f"completed {patient_index}/{len(pairs)} patients")

    payload = {
        "protocol_id": "clarifytrial-full-workflow-evaluation-v1",
        "model": model_label,
        "split": split,
        "patient_count": len(pairs),
        "arms": list(_ARMS),
        "action_budget": action_budget,
        "concurrency": concurrency,
        "case_results": str(case_path),
        "arm_metrics": _aggregate(rows),
        "paired_clarifytrial_vs_fixed": _paired(rows),
    }
    summary_path = output_dir / "summary.json"
    summary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


__all__ = ["run_full_workflow_evaluation"]
