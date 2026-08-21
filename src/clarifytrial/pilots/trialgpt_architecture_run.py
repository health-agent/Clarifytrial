"""Durable ChatGPT-subscription runner for the TrialGPT architecture benchmark."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any

from .trialgpt_architecture import (
    ArchitectureArmResult,
    ArchitectureCasePlan,
    JUDGMENT_BATCHING_ID,
    MAX_CRITERIA_PER_JUDGMENT_CALL,
    REVIEW_TRIGGER_ID,
    STATIC_ARCHITECTURE_PROTOCOL_ID,
    STATIC_COORDINATOR_RULE_ID,
    RunStatus,
    TrialGPTArchitectureBenchmark,
    assemble_trialgpt_architecture_benchmark,
    build_architecture_case,
    plan_architecture_arm_orders,
    run_trialgpt_architecture_case,
)
from ..datasets.trialgpt import (
    TrialGPTPair,
    group_patient_trial_pairs,
    load_sigir_trial_metadata,
    load_trialgpt_rows,
    split_trialgpt_pairs_by_patient,
)
from ..experiment_tracking import (
    CallStatus,
    ExperimentArm,
    ExperimentStage,
    ExperimentTracker,
    RateLimitWindow,
    SchemaStatus,
    SubscriptionRateLimitSnapshot,
    TransportStatus,
    decide_subscription_pause,
    record_from_model_usage,
)
from ..llm.codex_subscription import (
    DEFAULT_CODEX_EFFORT,
    DEFAULT_CODEX_MODEL,
    CodexSubscriptionModelPool,
    CodexSubscriptionStructuredModel,
)


SMOKE_PAIR_IDS = (
    "sigir-201512/NCT02418169",
    "sigir-20143/NCT02490059",
)

ProgressCallback = Callable[[str], None]


class ArchitectureExperimentPaused(RuntimeError):
    """The configured subscription-usage boundary was reached safely."""


class ArchitectureExperimentIncomplete(RuntimeError):
    """One or more case workers failed outside the arm-level error boundary."""


def _write_json_atomic(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line)


def _read_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _load_checkpoint_results(
    path: Path,
) -> dict[str, tuple[ArchitectureArmResult, ArchitectureArmResult, ArchitectureArmResult]]:
    if not path.exists():
        return {}
    latest: dict[
        str,
        tuple[ArchitectureArmResult, ArchitectureArmResult, ArchitectureArmResult],
    ] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                plan = ArchitectureCasePlan.model_validate(payload["plan"])
                results = tuple(
                    ArchitectureArmResult.model_validate(item)
                    for item in payload["results"]
                )
            except Exception as exc:
                raise ValueError(
                    f"invalid architecture checkpoint at line {line_number}"
                ) from exc
            if len(results) != 3 or tuple(item.arm for item in results) != plan.arm_order:
                raise ValueError(
                    f"architecture checkpoint line {line_number} violates its plan"
                )
            latest[plan.case_id] = results  # append-only retries use the latest case row
    return latest


def _load_checkpoint_numbers(path: Path) -> dict[str, int]:
    """Return the latest durable JSONL line number for each checkpointed case."""

    if not path.exists():
        return {}
    latest: dict[str, int] = {}
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
                case_id = str(payload["plan"]["case_id"])
            except Exception as exc:
                raise ValueError(
                    f"invalid architecture checkpoint at line {line_number}"
                ) from exc
            latest[case_id] = line_number
    return latest


def _validate_case_concurrency(value: int) -> int:
    if not 1 <= value <= 3:
        raise ValueError("case_concurrency must be between 1 and 3")
    return value


_PLAN_CORE_KEYS = (
    "experiment_id",
    "model",
    "effort",
    "order_seed",
    "pair_count",
    "criterion_count",
    "plans",
)
_PLAN_OPTIONAL_LOGICAL_KEYS = (
    "stage",
    "split_seed",
    "retrieval_top_k",
    "judgment_batching_id",
    "max_criteria_per_judgment_call",
    "protocol_id",
    "static_coordinator_rule_id",
    "review_trigger_id",
)
_EXECUTION_INVARIANTS = {
    "mode": "case_parallel_v1",
    "batch_unit": "case",
    "checkpoint_unit": "case",
    "tracker_writer": "main_thread",
    "judgment_batching_id": JUDGMENT_BATCHING_ID,
    "max_criteria_per_judgment_call": MAX_CRITERIA_PER_JUDGMENT_CALL,
    "protocol_id": STATIC_ARCHITECTURE_PROTOCOL_ID,
    "static_coordinator_rule_id": STATIC_COORDINATOR_RULE_ID,
    "review_trigger_id": REVIEW_TRIGGER_ID,
}


def _migrate_or_validate_plan(
    path: Path,
    expected: Mapping[str, Any],
    *,
    case_concurrency: int,
) -> dict[str, Any]:
    """Validate the immutable plan, then atomically add execution metadata.

    The first dev20 run wrote only the seven core keys below.  Its frozen case
    IDs and arm orders already encode the selected pairs, BM25 snapshot, and
    order seed, so missing newer metadata is migrated without invalidating its
    durable case checkpoints.
    """

    concurrency = _validate_case_concurrency(case_concurrency)
    if path.exists():
        existing = _read_json(path)
        if not isinstance(existing, Mapping):
            raise ValueError("existing architecture plan is not a JSON object")
        for key in _PLAN_CORE_KEYS:
            if key not in existing or existing[key] != expected[key]:
                raise ValueError(
                    f"existing architecture plan core does not match: {key}"
                )
        for key in _PLAN_OPTIONAL_LOGICAL_KEYS:
            if key in existing and existing[key] != expected[key]:
                raise ValueError(
                    f"existing architecture plan metadata does not match: {key}"
                )
        migrated = dict(existing)
    else:
        migrated = dict(expected)

    for key in _PLAN_OPTIONAL_LOGICAL_KEYS:
        migrated[key] = expected[key]

    execution_value = migrated.get("execution")
    if execution_value is None:
        execution: dict[str, Any] = dict(_EXECUTION_INVARIANTS)
        history: list[int] = []
    elif isinstance(execution_value, Mapping):
        execution = dict(execution_value)
        for key, value in _EXECUTION_INVARIANTS.items():
            if key in execution and execution[key] != value:
                raise ValueError(
                    f"existing architecture execution plan does not match: {key}"
                )
            execution[key] = value
        raw_history = execution.get("case_concurrency_history", [])
        if not isinstance(raw_history, list):
            raise ValueError("case_concurrency_history must be a list")
        history = [_validate_case_concurrency(int(item)) for item in raw_history]
        previous = execution.get("case_concurrency")
        if previous is not None:
            previous_value = _validate_case_concurrency(int(previous))
            if previous_value not in history:
                history.append(previous_value)
    else:
        raise ValueError("existing architecture execution plan is invalid")
    if concurrency not in history:
        history.append(concurrency)
    execution["case_concurrency"] = concurrency
    execution["case_concurrency_history"] = history
    migrated["execution"] = execution

    current = _read_json(path) if path.exists() else None
    if current != migrated:
        _write_json_atomic(path, migrated)
    return migrated


def _validate_checkpoints_against_plans(
    checkpoints: Mapping[
        str,
        tuple[ArchitectureArmResult, ArchitectureArmResult, ArchitectureArmResult],
    ],
    plans: Sequence[ArchitectureCasePlan],
) -> None:
    plan_by_case = {plan.case_id: plan for plan in plans}
    for case_id, results in checkpoints.items():
        plan = plan_by_case.get(case_id)
        if plan is None:
            raise ValueError("architecture checkpoint references an unknown case")
        if any(
            item.case_id != plan.case_id
            or item.pair_id != plan.pair_id
            or item.planned_arm_order != plan.arm_order
            for item in results
        ):
            raise ValueError("architecture checkpoint does not match the current plan")


def _stage_pairs(
    all_pairs: Sequence[TrialGPTPair],
    *,
    stage: ExperimentStage,
    seed: int,
) -> list[TrialGPTPair]:
    split = split_trialgpt_pairs_by_patient(all_pairs, seed=seed)
    if stage is ExperimentStage.SMOKE:
        by_id = {f"{item.patient_id}/{item.trial_id}": item for item in split.development_pairs}
        missing = [item for item in SMOKE_PAIR_IDS if item not in by_id]
        if missing:
            raise ValueError("smoke pairs are missing from the fixed development split")
        return [by_id[item] for item in SMOKE_PAIR_IDS]
    if stage is ExperimentStage.DEV:
        return list(split.development_pairs)
    if stage is ExperimentStage.MAIN:
        return list(split.held_out_pairs)
    if stage is ExperimentStage.OVERLAP:
        return list(split.overlap_patient_pairs)
    raise ValueError("sensitivity runs require an explicit disagreement manifest")


def _window(value: Any) -> RateLimitWindow | None:
    if not isinstance(value, Mapping):
        return None
    used = value.get("usedPercent", value.get("used_percent"))
    if used is None:
        return None
    return RateLimitWindow(
        used_percent=float(used),
        window_duration_minutes=value.get(
            "windowDurationMins", value.get("window_duration_mins")
        ),
        resets_at=value.get("resetsAt", value.get("resets_at")),
    )


def _subscription_snapshot(
    rate_limits: Mapping[str, Any],
    account_usage: Mapping[str, Any],
    *,
    baseline_lifetime_tokens: float | None,
) -> SubscriptionRateLimitSnapshot:
    current = rate_limits.get("rateLimits", rate_limits.get("rate_limits", {}))
    if not isinstance(current, Mapping):
        current = {}
    summary = account_usage.get("summary", {})
    if not isinstance(summary, Mapping):
        summary = {}
    lifetime = summary.get("lifetimeTokens", summary.get("lifetime_tokens"))
    lifetime_number = None if lifetime is None else float(lifetime)
    delta = (
        None
        if lifetime_number is None or baseline_lifetime_tokens is None
        else max(0.0, lifetime_number - baseline_lifetime_tokens)
    )
    return SubscriptionRateLimitSnapshot(
        observed_at=datetime.now(timezone.utc),
        limit_id=str(current.get("limitId", current.get("limit_id", "codex"))),
        primary=_window(current.get("primary")),
        secondary=_window(current.get("secondary")),
        account_usage_lifetime=lifetime_number,
        account_usage_daily_delta=delta,
    )


def _read_subscription_snapshot(
    model: CodexSubscriptionStructuredModel,
    *,
    baseline_lifetime_tokens: float | None,
) -> SubscriptionRateLimitSnapshot:
    return _subscription_snapshot(
        model.rate_limits(),
        model.account_usage(),
        baseline_lifetime_tokens=baseline_lifetime_tokens,
    )


def _record_case_calls(
    tracker: ExperimentTracker,
    results: Sequence[ArchitectureArmResult],
    pair: TrialGPTPair,
    *,
    experiment_id: str,
    stage: ExperimentStage,
    checkpoint_number: int,
    rate_limit_before: SubscriptionRateLimitSnapshot | None,
    rate_limit_after: SubscriptionRateLimitSnapshot | None,
) -> None:
    for arm_result in results:
        for index, call in enumerate(arm_result.calls, start=1):
            usage = dict(call.usage or {})
            usage.setdefault("model_id", DEFAULT_CODEX_MODEL)
            usage.setdefault("effort", DEFAULT_CODEX_EFFORT)
            usage.setdefault("attempts", 1)
            completed = call.status == "completed"
            tracker.append(
                record_from_model_usage(
                    usage,
                    experiment_id=experiment_id,
                    stable_task_id=(
                        f"{call.call_id}:checkpoint-{checkpoint_number}:call-{index}"
                    ),
                    stage=stage,
                    arm=ExperimentArm(arm_result.arm.value),
                    patient_id=pair.patient_id,
                    trial_id=pair.trial_id,
                    pair_id=arm_result.pair_id,
                    role=call.role,
                    requested_model=DEFAULT_CODEX_MODEL,
                    requested_effort=DEFAULT_CODEX_EFFORT,
                    schema_status=(SchemaStatus.VALID if completed else SchemaStatus.INVALID),
                    transport_status=(
                        TransportStatus.SUCCEEDED if completed else TransportStatus.FAILED
                    ),
                    status=(CallStatus.COMPLETED if completed else CallStatus.FAILED),
                    rate_limit_before=rate_limit_before,
                    rate_limit_after=rate_limit_after,
                    call_id=usage.get("request_id"),
                    thread_id=usage.get("thread_id"),
                    turn_id=usage.get("turn_id"),
                    error_code=call.error_type,
                )
            )


def _newly_executed_arm_results(
    prior_results: Sequence[ArchitectureArmResult],
    merged_results: Sequence[ArchitectureArmResult],
) -> tuple[ArchitectureArmResult, ...]:
    """Return arms that were not already complete before this retry."""

    completed_before = {
        item.arm for item in prior_results if item.status is RunStatus.COMPLETED
    }
    return tuple(
        item for item in merged_results if item.arm not in completed_before
    )


def run_subscription_architecture_stage(
    *,
    raw_jsonl: str | Path,
    sigir_corpus: str | Path,
    output_dir: str | Path,
    stage: ExperimentStage | str,
    experiment_id: str,
    split_seed: int = 20_260_820,
    order_seed: int = 20_260_821,
    retrieval_top_k: int = 5,
    pause_threshold_percent: float = 80.0,
    timeout_seconds: float = 180.0,
    case_concurrency: int = 3,
    progress: ProgressCallback | None = None,
) -> Path:
    """Run or resume one fixed stage with up to three concurrent case workers.

    Each worker executes its case's frozen arm order serially.  Only this main
    thread appends checkpoints, tracker rows, progress, and status files.
    """

    resolved_stage = ExperimentStage(stage)
    concurrency = _validate_case_concurrency(case_concurrency)
    destination = Path(output_dir)
    checkpoint_path = destination / "case-results.jsonl"
    tracker = ExperimentTracker(
        destination / "calls.jsonl", destination / "usage-summary.json"
    )
    rows = load_trialgpt_rows(raw_jsonl)
    metadata = load_sigir_trial_metadata(sigir_corpus)
    all_pairs = group_patient_trial_pairs(rows, metadata)
    pairs = _stage_pairs(all_pairs, stage=resolved_stage, seed=split_seed)
    cases = [build_architecture_case(item, retrieval_top_k=retrieval_top_k) for item in pairs]
    plans = plan_architecture_arm_orders(cases, seed=order_seed)
    pair_by_case = {
        case.case_id: pair for case, pair in zip(cases, pairs, strict=True)
    }
    case_by_id = {case.case_id: case for case in cases}
    plan_payload = {
        "experiment_id": experiment_id,
        "stage": resolved_stage.value,
        "model": DEFAULT_CODEX_MODEL,
        "effort": DEFAULT_CODEX_EFFORT,
        "split_seed": split_seed,
        "order_seed": order_seed,
        "retrieval_top_k": retrieval_top_k,
        "judgment_batching_id": JUDGMENT_BATCHING_ID,
        "max_criteria_per_judgment_call": MAX_CRITERIA_PER_JUDGMENT_CALL,
        "protocol_id": STATIC_ARCHITECTURE_PROTOCOL_ID,
        "static_coordinator_rule_id": STATIC_COORDINATOR_RULE_ID,
        "review_trigger_id": REVIEW_TRIGGER_ID,
        "pair_count": len(pairs),
        "criterion_count": sum(len(item.criteria) for item in pairs),
        "plans": [item.model_dump(mode="json") for item in plans],
    }
    plan_path = destination / "plan.json"
    _migrate_or_validate_plan(
        plan_path,
        plan_payload,
        case_concurrency=concurrency,
    )

    checkpoints = _load_checkpoint_results(checkpoint_path)
    checkpoint_numbers = _load_checkpoint_numbers(checkpoint_path)
    _validate_checkpoints_against_plans(checkpoints, plans)
    # Reconcile the narrow crash window where the gold-free checkpoint reached
    # disk but the derived usage journal did not. Stable checkpoint line IDs
    # make existing tracker rows idempotent, including the first 15 dev cases.
    for case_id, results in checkpoints.items():
        _record_case_calls(
            tracker,
            results,
            pair_by_case[case_id],
            experiment_id=experiment_id,
            stage=resolved_stage,
            checkpoint_number=checkpoint_numbers[case_id],
            rate_limit_before=None,
            rate_limit_after=None,
        )

    completed_by_case = dict(checkpoints)
    pending_plans: list[ArchitectureCasePlan] = []
    for position, plan in enumerate(plans, start=1):
        previous = checkpoints.get(plan.case_id)
        if previous is not None and all(
            item.status is RunStatus.COMPLETED for item in previous
        ):
            if progress is not None:
                progress(f"{position}/{len(plans)} {plan.pair_id}: resumed")
        else:
            pending_plans.append(plan)
    started_at = time.perf_counter()
    with CodexSubscriptionModelPool(
        size=concurrency,
        worker_factory=lambda: CodexSubscriptionStructuredModel(
            timeout_seconds=timeout_seconds
        ),
    ) as model:
        account = model.account_info()
        models = model.available_models()
        if not any(
            isinstance(item, Mapping)
            and (item.get("model") == DEFAULT_CODEX_MODEL or item.get("id") == DEFAULT_CODEX_MODEL)
            for item in models.get("data", [])
        ):
            raise RuntimeError(f"{DEFAULT_CODEX_MODEL} is not available to this account")
        initial_usage = model.account_usage()
        initial_summary = initial_usage.get("summary", {})
        baseline_lifetime = (
            initial_summary.get("lifetimeTokens")
            if isinstance(initial_summary, Mapping)
            else None
        )
        before_stage = _read_subscription_snapshot(
            model, baseline_lifetime_tokens=baseline_lifetime
        )
        _write_json_atomic(
            destination / "account-before.json",
            {
                "account": account,
                "rate_limit": before_stage.model_dump(mode="json"),
                "runtime": asdict(model.runtime_metadata()),
            },
        )

        worker_errors: list[dict[str, Any]] = []
        pause_request: Any | None = None
        next_pending_index = 0
        inflight: dict[
            Future[
                tuple[
                    ArchitectureArmResult,
                    ArchitectureArmResult,
                    ArchitectureArmResult,
                ]
            ],
            tuple[
                ArchitectureCasePlan,
                SubscriptionRateLimitSnapshot,
                tuple[ArchitectureArmResult, ...],
            ],
        ] = {}

        def submit_available(executor: ThreadPoolExecutor) -> None:
            nonlocal next_pending_index, pause_request
            while (
                pause_request is None
                and len(inflight) < concurrency
                and next_pending_index < len(pending_plans)
            ):
                rate_before = _read_subscription_snapshot(
                    model, baseline_lifetime_tokens=baseline_lifetime
                )
                pause = decide_subscription_pause(
                    rate_before, threshold_percent=pause_threshold_percent
                )
                if pause.pause:
                    pause_request = pause
                    return
                plan = pending_plans[next_pending_index]
                next_pending_index += 1
                previous = tuple(checkpoints.get(plan.case_id, ()))
                future = executor.submit(
                    run_trialgpt_architecture_case,
                    case_by_id[plan.case_id],
                    plan,
                    model,
                    prior_results=previous,
                )
                inflight[future] = (plan, rate_before, previous)
                if progress is not None:
                    progress(
                        f"submitted {plan.execution_rank + 1}/{len(plans)} "
                        f"{plan.pair_id}; in_flight={len(inflight)}"
                    )

        with ThreadPoolExecutor(
            max_workers=concurrency,
            thread_name_prefix="trialgpt-case",
        ) as executor:
            submit_available(executor)
            while inflight:
                done, _ = wait(tuple(inflight), return_when=FIRST_COMPLETED)
                for future in sorted(
                    done,
                    key=lambda item: inflight[item][0].execution_rank,
                ):
                    plan, rate_before, previous = inflight.pop(future)
                    try:
                        results = future.result()
                    except Exception as exc:
                        error = {
                            "case_id": plan.case_id,
                            "pair_id": plan.pair_id,
                            "execution_rank": plan.execution_rank,
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:2_000],
                        }
                        worker_errors.append(error)
                        _append_jsonl(destination / "case-errors.jsonl", error)
                        if progress is not None:
                            progress(
                                f"failed {plan.execution_rank + 1}/{len(plans)} "
                                f"{plan.pair_id}: {type(exc).__name__}"
                            )
                        continue

                    # Main-thread single writer: checkpoint first because it
                    # already contains every usage record needed to reconcile
                    # the tracker after a crash.
                    checkpoint_number = _append_jsonl(
                        checkpoint_path,
                        {
                            "plan": plan.model_dump(mode="json"),
                            "results": [
                                item.model_dump(mode="json") for item in results
                            ],
                        },
                    )
                    checkpoints[plan.case_id] = results
                    checkpoint_numbers[plan.case_id] = checkpoint_number
                    completed_by_case[plan.case_id] = results
                    newly_executed = _newly_executed_arm_results(
                        previous, results
                    )
                    try:
                        rate_after = _read_subscription_snapshot(
                            model, baseline_lifetime_tokens=baseline_lifetime
                        )
                    except Exception:
                        rate_after = None
                    _record_case_calls(
                        tracker,
                        newly_executed,
                        pair_by_case[plan.case_id],
                        experiment_id=experiment_id,
                        stage=resolved_stage,
                        checkpoint_number=checkpoint_number,
                        rate_limit_before=rate_before,
                        rate_limit_after=rate_after,
                    )
                    if progress is not None:
                        completed_calls = sum(
                            len(item.calls) for item in newly_executed
                        )
                        completed_count = sum(
                            all(item.status is RunStatus.COMPLETED for item in rows)
                            for rows in completed_by_case.values()
                        )
                        progress(
                            f"completed {completed_count}/{len(plans)} "
                            f"{plan.pair_id}: {len(newly_executed)} new arms, "
                            f"{completed_calls} new calls; "
                            f"in_flight={len(inflight)}"
                        )
                submit_available(executor)

        completed_count = sum(
            all(item.status is RunStatus.COMPLETED for item in rows)
            for rows in completed_by_case.values()
        )
        if pause_request is not None and next_pending_index < len(pending_plans):
            _write_json_atomic(
                destination / "run-status.json",
                {
                    "status": "paused",
                    "completed_cases": completed_count,
                    "checkpointed_cases": len(completed_by_case),
                    "total_cases": len(plans),
                    "case_concurrency": concurrency,
                    "pause": pause_request.model_dump(mode="json"),
                },
            )
            raise ArchitectureExperimentPaused(
                f"subscription usage reached {pause_request.observed_percent}%"
            )

        if worker_errors:
            _write_json_atomic(
                destination / "run-status.json",
                {
                    "status": "incomplete",
                    "completed_cases": completed_count,
                    "checkpointed_cases": len(completed_by_case),
                    "total_cases": len(plans),
                    "case_concurrency": concurrency,
                    "worker_error_count": len(worker_errors),
                },
            )
            raise ArchitectureExperimentIncomplete(
                f"{len(worker_errors)} case worker(s) failed; completed cases were checkpointed"
            )

        missing_case_ids = [
            plan.case_id for plan in plans if plan.case_id not in completed_by_case
        ]
        if missing_case_ids:
            raise ArchitectureExperimentIncomplete(
                "architecture cases are missing durable results: "
                + ", ".join(missing_case_ids)
            )
        raw_results = [
            result
            for plan in plans
            for result in completed_by_case[plan.case_id]
        ]

        benchmark = assemble_trialgpt_architecture_benchmark(
            pairs,
            plans,
            raw_results,
            order_seed=order_seed,
            retrieval_top_k=retrieval_top_k,
        )
        benchmark_path = destination / "benchmark.json"
        _write_json_atomic(benchmark_path, benchmark.model_dump(mode="json"))
        after_stage = _read_subscription_snapshot(
            model, baseline_lifetime_tokens=baseline_lifetime
        )
        _write_json_atomic(
            destination / "account-after.json",
            {"rate_limit": after_stage.model_dump(mode="json")},
        )
        _write_json_atomic(
            destination / "run-status.json",
            {
                "status": "completed",
                "completed_cases": len(plans),
                "total_cases": len(plans),
                "failed_arms": sum(
                    item.status is not RunStatus.COMPLETED for item in benchmark.results
                ),
                "case_concurrency": concurrency,
                "elapsed_seconds": round(time.perf_counter() - started_at, 3),
                "usage_summary": tracker.summary().model_dump(mode="json"),
            },
        )
        return benchmark_path


__all__ = [
    "ArchitectureExperimentIncomplete",
    "ArchitectureExperimentPaused",
    "SMOKE_PAIR_IDS",
    "run_subscription_architecture_stage",
]
