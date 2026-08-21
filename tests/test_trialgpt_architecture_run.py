from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from clarifytrial.cli import _parser
from clarifytrial.experiment_tracking import ExperimentStage
from clarifytrial.llm.codex_subscription import CodexRuntimeMetadata
from clarifytrial.pilots import trialgpt_architecture_run as runmod
from clarifytrial.pilots.trialgpt_architecture import (
    ARM_ROTATIONS,
    ArchitectureArmResult,
    ArchitectureCallRecord,
    ArchitectureCasePlan,
    JUDGMENT_BATCHING_ID,
    MAX_CRITERIA_PER_JUDGMENT_CALL,
    RunStatus,
)


def _results(plan: ArchitectureCasePlan):
    return tuple(
        ArchitectureArmResult(
            run_id=f"{plan.case_id}:{arm.value}",
            case_id=plan.case_id,
            pair_id=plan.pair_id,
            arm=arm,
            planned_arm_order=plan.arm_order,
            status=RunStatus.COMPLETED,
            role_call_counts={},
            calls=(),
            trace=(),
        )
        for arm in plan.arm_order
    )


def test_parallel_runner_resumes_legacy_15_of_20_and_uses_three_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pairs = [
        SimpleNamespace(
            patient_id=f"patient-{index}",
            trial_id=f"trial-{index}",
            criteria=[SimpleNamespace()],
        )
        for index in range(20)
    ]
    cases = [
        SimpleNamespace(case_id=f"case-{index}", pair_id=f"patient-{index}/trial-{index}")
        for index in range(20)
    ]
    plans = tuple(
        ArchitectureCasePlan(
            case_id=case.case_id,
            pair_id=case.pair_id,
            execution_rank=index,
            arm_order=ARM_ROTATIONS[index % 3],
        )
        for index, case in enumerate(cases)
    )
    output = tmp_path / "run"
    output.mkdir()
    legacy_plan = {
        "criterion_count": 20,
        "effort": runmod.DEFAULT_CODEX_EFFORT,
        "experiment_id": "legacy-dev20",
        "model": runmod.DEFAULT_CODEX_MODEL,
        "order_seed": 7,
        "pair_count": 20,
        "plans": [item.model_dump(mode="json") for item in plans],
    }
    (output / "plan.json").write_text(json.dumps(legacy_plan), encoding="utf-8")
    with (output / "case-results.jsonl").open("w", encoding="utf-8") as handle:
        for plan in plans[:15]:
            handle.write(
                json.dumps(
                    {
                        "plan": plan.model_dump(mode="json"),
                        "results": [item.model_dump(mode="json") for item in _results(plan)],
                    }
                )
                + "\n"
            )

    created_timeouts: list[float] = []

    class FakeWorker:
        def __init__(self, *, timeout_seconds: float) -> None:
            created_timeouts.append(timeout_seconds)

    class FakePool:
        def __init__(self, *, size: int, worker_factory) -> None:
            assert size == 3
            self.workers = [worker_factory() for _ in range(size)]

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def account_info(self):
            return {}

        def available_models(self):
            return {"data": [{"id": runmod.DEFAULT_CODEX_MODEL}]}

        def account_usage(self):
            return {"summary": {"lifetimeTokens": 100}}

        def rate_limits(self):
            return {"rateLimits": {"limitId": "codex", "primary": {"usedPercent": 1}}}

        def runtime_metadata(self):
            return CodexRuntimeMetadata(runtime_name="fake")

    active = 0
    max_active = 0
    called: list[str] = []
    lock = threading.Lock()

    def fake_case_runner(case, plan, model, *, prior_results=()):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        time.sleep(0.03)
        with lock:
            active -= 1
            called.append(plan.case_id)
        return _results(plan)

    class FakeBenchmark:
        def __init__(self, raw_results) -> None:
            self.results = tuple(raw_results)

        def model_dump(self, *, mode: str):
            return {"results": [item.model_dump(mode=mode) for item in self.results]}

    monkeypatch.setattr(runmod, "CodexSubscriptionStructuredModel", FakeWorker)
    monkeypatch.setattr(runmod, "CodexSubscriptionModelPool", FakePool)
    monkeypatch.setattr(runmod, "load_trialgpt_rows", lambda path: [])
    monkeypatch.setattr(runmod, "load_sigir_trial_metadata", lambda path: {})
    monkeypatch.setattr(runmod, "group_patient_trial_pairs", lambda rows, metadata: pairs)
    monkeypatch.setattr(runmod, "_stage_pairs", lambda all_pairs, stage, seed: list(all_pairs))
    monkeypatch.setattr(
        runmod,
        "build_architecture_case",
        lambda pair, retrieval_top_k: cases[int(pair.patient_id.split("-")[-1])],
    )
    monkeypatch.setattr(runmod, "plan_architecture_arm_orders", lambda values, seed: plans)
    monkeypatch.setattr(runmod, "run_trialgpt_architecture_case", fake_case_runner)
    monkeypatch.setattr(
        runmod,
        "assemble_trialgpt_architecture_benchmark",
        lambda pairs, plans, raw_results, **kwargs: FakeBenchmark(raw_results),
    )

    result = runmod.run_subscription_architecture_stage(
        raw_jsonl=tmp_path / "rows.jsonl",
        sigir_corpus=tmp_path / "corpus.jsonl",
        output_dir=output,
        stage=ExperimentStage.DEV,
        experiment_id="legacy-dev20",
        split_seed=11,
        order_seed=7,
        retrieval_top_k=5,
        timeout_seconds=42,
        case_concurrency=3,
    )

    assert result == output / "benchmark.json"
    assert set(called) == {f"case-{index}" for index in range(15, 20)}
    assert max_active == 3
    assert created_timeouts == [42, 42, 42]
    assert len((output / "case-results.jsonl").read_text().splitlines()) == 20
    migrated = json.loads((output / "plan.json").read_text())
    assert migrated["stage"] == "dev"
    assert migrated["split_seed"] == 11
    assert migrated["retrieval_top_k"] == 5
    assert migrated["judgment_batching_id"] == JUDGMENT_BATCHING_ID
    assert migrated["max_criteria_per_judgment_call"] == MAX_CRITERIA_PER_JUDGMENT_CALL
    assert migrated["execution"]["case_concurrency"] == 3
    assert migrated["execution"]["tracker_writer"] == "main_thread"


def test_cli_case_concurrency_defaults_to_three_and_caps_at_three() -> None:
    base = [
        "run-trialgpt-architecture",
        "--raw-jsonl",
        "rows.jsonl",
        "--sigir-corpus",
        "corpus.jsonl",
        "--output",
        "out",
        "--stage",
        "dev",
    ]
    assert _parser().parse_args(base).case_concurrency == 3
    assert _parser().parse_args([*base, "--case-concurrency", "2"]).case_concurrency == 2
    with pytest.raises(SystemExit):
        _parser().parse_args([*base, "--case-concurrency", "4"])


def test_retry_tracker_records_only_the_newly_executed_m2_calls() -> None:
    plan = ArchitectureCasePlan(
        case_id="case-retry",
        pair_id="patient-retry/trial-retry",
        execution_rank=0,
        arm_order=ARM_ROTATIONS[0],
    )
    initial = _results(plan)
    prior = tuple(
        item
        if item.arm.value != "M2"
        else item.model_copy(update={"status": RunStatus.FAILED})
        for item in initial
    )
    merged = tuple(
        item.model_copy(
            update={
                "status": RunStatus.COMPLETED,
                "calls": (
                    ArchitectureCallRecord(
                        call_id=f"new-{item.arm.value}",
                        role="matcher_judge",
                        prompt_id="prompt-v2",
                        status="completed",
                        usage={
                            "model_id": "gpt-5.6-sol",
                            "effort": "medium",
                            "input_tokens": 10,
                            "output_tokens": 2,
                            "total_tokens": 12,
                        },
                    ),
                ),
            }
        )
        for item in initial
    )
    newly_executed = runmod._newly_executed_arm_results(prior, merged)
    appended = []

    class FakeTracker:
        def append(self, record):
            appended.append(record)

    runmod._record_case_calls(
        FakeTracker(),
        newly_executed,
        SimpleNamespace(patient_id="patient-retry", trial_id="trial-retry"),
        experiment_id="retry-test",
        stage=ExperimentStage.DEV,
        checkpoint_number=2,
        rate_limit_before=None,
        rate_limit_after=None,
    )

    assert [item.arm.value for item in newly_executed] == ["M2"]
    assert len(appended) == 1
    assert appended[0].arm.value == "M2"
    assert appended[0].stable_task_id.startswith("new-M2:checkpoint-2")
