"""Durable subscription runner for the strong single/reviewer comparison."""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from collections.abc import Callable, Mapping
from dataclasses import asdict
import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any, Literal

from ..datasets.trialgpt import (
    TrialGPTPair,
    group_patient_trial_pairs,
    load_sigir_trial_metadata,
    load_trialgpt_rows,
    split_trialgpt_pairs_by_patient,
)
from ..llm.codex_subscription import (
    DEFAULT_CODEX_EFFORT,
    DEFAULT_CODEX_MODEL,
    CodexSubscriptionModelPool,
    CodexSubscriptionStructuredModel,
)
from .trialgpt_review_benchmark import (
    NO_WEB_REVIEW_PROMPT_ID,
    PROTOCOL_ID,
    SINGLE_PROMPT_ID,
    WEB_REVIEW_PROMPT_ID,
    StrongReviewCaseResult,
    assemble_strong_review_benchmark,
    run_strong_review_case,
)


ReviewStage = Literal["development", "heldout", "overlap"]
ProgressCallback = Callable[[str], None]


class StrongReviewExperimentIncomplete(RuntimeError):
    """One or more cases failed after completed cases were checkpointed."""


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


def _append_jsonl(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _load_checkpoints(path: Path) -> dict[str, StrongReviewCaseResult]:
    latest: dict[str, StrongReviewCaseResult] = {}
    if not path.exists():
        return latest
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                result = StrongReviewCaseResult.model_validate(json.loads(line))
            except Exception as exc:
                raise ValueError(
                    f"invalid strong-review checkpoint at line {line_number}"
                ) from exc
            latest[result.pair_id] = result
    return latest


def _selected_pairs(
    pairs: list[TrialGPTPair],
    *,
    stage: ReviewStage,
    limit: int | None,
) -> list[TrialGPTPair]:
    split = split_trialgpt_pairs_by_patient(pairs)
    selected = {
        "development": list(split.development_pairs),
        "heldout": list(split.held_out_pairs),
        "overlap": list(split.overlap_patient_pairs),
    }[stage]
    if limit is not None:
        if limit <= 0:
            raise ValueError("limit must be positive")
        selected = selected[:limit]
    return selected


def _plan_payload(
    pairs: list[TrialGPTPair],
    *,
    stage: ReviewStage,
    retrieval_top_k: int,
    case_concurrency: int,
) -> dict[str, Any]:
    return {
        "protocol_id": PROTOCOL_ID,
        "model": DEFAULT_CODEX_MODEL,
        "effort": DEFAULT_CODEX_EFFORT,
        "stage": stage,
        "pair_count": len(pairs),
        "criterion_count": sum(len(item.criteria) for item in pairs),
        "pair_ids": [f"{item.patient_id}/{item.trial_id}" for item in pairs],
        "retrieval_top_k": retrieval_top_k,
        "case_concurrency": case_concurrency,
        "single_prompt_id": SINGLE_PROMPT_ID,
        "no_web_review_prompt_id": NO_WEB_REVIEW_PROMPT_ID,
        "web_review_prompt_id": WEB_REVIEW_PROMPT_ID,
        "web_policy": {
            "mode": "live",
            "maximum_search_queries_per_review_call": 3,
            "allowed": "general medical concepts and documentation practice",
            "forbidden": "patient text, identifiers, trial ID, benchmark, answer label",
        },
    }


def run_subscription_strong_review_stage(
    *,
    raw_jsonl: str | Path,
    sigir_corpus: str | Path,
    output_dir: str | Path,
    stage: ReviewStage = "development",
    limit: int | None = None,
    retrieval_top_k: int = 5,
    timeout_seconds: float = 240.0,
    case_concurrency: int = 3,
    progress: ProgressCallback | None = None,
) -> Path:
    if stage not in {"development", "heldout", "overlap"}:
        raise ValueError("unknown strong-review stage")
    if not 1 <= case_concurrency <= 3:
        raise ValueError("case_concurrency must be between 1 and 3")
    destination = Path(output_dir)
    checkpoint_path = destination / "case-results.jsonl"
    rows = load_trialgpt_rows(raw_jsonl)
    metadata = load_sigir_trial_metadata(sigir_corpus)
    pairs = _selected_pairs(
        group_patient_trial_pairs(rows, metadata), stage=stage, limit=limit
    )
    pair_by_id = {f"{item.patient_id}/{item.trial_id}": item for item in pairs}
    if len(pair_by_id) != len(pairs):
        raise ValueError("selected strong-review pairs repeat an ID")
    plan = _plan_payload(
        pairs,
        stage=stage,
        retrieval_top_k=retrieval_top_k,
        case_concurrency=case_concurrency,
    )
    plan_path = destination / "plan.json"
    if plan_path.exists():
        existing = json.loads(plan_path.read_text(encoding="utf-8"))
        if existing != plan:
            raise ValueError("existing strong-review plan does not match this run")
    else:
        _write_json_atomic(plan_path, plan)

    completed = _load_checkpoints(checkpoint_path)
    if not set(completed) <= set(pair_by_id):
        raise ValueError("checkpoint contains a pair outside the frozen plan")
    pending = [pair for pair_id, pair in pair_by_id.items() if pair_id not in completed]
    started = time.perf_counter()
    worker_errors: list[dict[str, Any]] = []

    with CodexSubscriptionModelPool(
        size=case_concurrency,
        worker_factory=lambda: CodexSubscriptionStructuredModel(
            timeout_seconds=timeout_seconds
        ),
    ) as no_web_model, CodexSubscriptionModelPool(
        size=case_concurrency,
        worker_factory=lambda: CodexSubscriptionStructuredModel(
            timeout_seconds=timeout_seconds,
            web_search=True,
        ),
    ) as web_model:
        account = no_web_model.account_info()
        models = no_web_model.available_models()
        if not any(
            isinstance(item, Mapping)
            and item.get("model") == DEFAULT_CODEX_MODEL
            for item in models.get("data", [])
        ):
            raise RuntimeError(f"{DEFAULT_CODEX_MODEL} is not available")
        before = no_web_model.rate_limits()
        _write_json_atomic(
            destination / "account-before.json",
            {
                "account": account,
                "rate_limits": before,
                "runtime": asdict(no_web_model.runtime_metadata()),
            },
        )

        inflight: dict[Future[StrongReviewCaseResult], TrialGPTPair] = {}
        next_index = 0

        def submit_available(executor: ThreadPoolExecutor) -> None:
            nonlocal next_index
            while len(inflight) < case_concurrency and next_index < len(pending):
                pair = pending[next_index]
                next_index += 1
                future = executor.submit(
                    run_strong_review_case,
                    pair,
                    no_web_model,
                    web_model,
                    retrieval_top_k=retrieval_top_k,
                )
                inflight[future] = pair
                if progress is not None:
                    progress(
                        f"submitted {len(completed) + len(inflight)}/{len(pairs)} "
                        f"{pair.patient_id}/{pair.trial_id}"
                    )

        with ThreadPoolExecutor(
            max_workers=case_concurrency,
            thread_name_prefix="trialgpt-strong-review",
        ) as executor:
            submit_available(executor)
            while inflight:
                done, _ = wait(tuple(inflight), return_when=FIRST_COMPLETED)
                for future in done:
                    pair = inflight.pop(future)
                    pair_id = f"{pair.patient_id}/{pair.trial_id}"
                    try:
                        result = future.result()
                    except Exception as exc:
                        error = {
                            "pair_id": pair_id,
                            "error_type": type(exc).__name__,
                            "error": str(exc)[:2_000],
                        }
                        worker_errors.append(error)
                        _append_jsonl(destination / "case-errors.jsonl", error)
                        if progress is not None:
                            progress(f"failed {pair_id}: {type(exc).__name__}")
                    else:
                        _append_jsonl(
                            checkpoint_path, result.model_dump(mode="json")
                        )
                        completed[pair_id] = result
                        if progress is not None:
                            progress(
                                f"completed {len(completed)}/{len(pairs)} {pair_id}; "
                                f"calls={len(result.calls)}"
                            )
                submit_available(executor)

        after = no_web_model.rate_limits()
        _write_json_atomic(destination / "account-after.json", {"rate_limits": after})

    if worker_errors or len(completed) != len(pairs):
        _write_json_atomic(
            destination / "run-status.json",
            {
                "status": "incomplete",
                "completed_cases": len(completed),
                "total_cases": len(pairs),
                "worker_error_count": len(worker_errors),
                "elapsed_seconds": round(time.perf_counter() - started, 3),
            },
        )
        raise StrongReviewExperimentIncomplete(
            f"{len(worker_errors)} case(s) failed; rerun the same command to resume"
        )

    ordered_results = [
        completed[f"{pair.patient_id}/{pair.trial_id}"] for pair in pairs
    ]
    benchmark = assemble_strong_review_benchmark(pairs, ordered_results)
    benchmark_path = destination / "benchmark.json"
    _write_json_atomic(benchmark_path, benchmark.model_dump(mode="json"))
    _write_json_atomic(
        destination / "run-status.json",
        {
            "status": "completed",
            "completed_cases": len(completed),
            "total_cases": len(pairs),
            "case_concurrency": case_concurrency,
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "executed_total_tokens": benchmark.executed_total_tokens,
            "web_search_actions": benchmark.web_search_actions,
        },
    )
    return benchmark_path


__all__ = [
    "StrongReviewExperimentIncomplete",
    "run_subscription_strong_review_stage",
]
