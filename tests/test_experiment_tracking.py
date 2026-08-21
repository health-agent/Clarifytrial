from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from clarifytrial.experiment_tracking import (
    CallStatus,
    ExperimentCallRecord,
    ExperimentTracker,
    RateLimitWindow,
    SchemaStatus,
    SubscriptionRateLimitSnapshot,
    TransportStatus,
    build_experiment_summary,
    decide_subscription_pause,
    record_from_model_usage,
)
from clarifytrial.llm.base import ModelUsage


NOW = datetime(2026, 8, 21, 3, 0, tzinfo=timezone.utc)


def make_snapshot(
    primary_percent: float,
    *,
    secondary_percent: float | None = None,
) -> SubscriptionRateLimitSnapshot:
    return SubscriptionRateLimitSnapshot(
        observed_at=NOW,
        limit_id="codex-subscription",
        primary=RateLimitWindow(
            used_percent=primary_percent,
            window_duration_minutes=300,
            resets_at=1_787_290_000,
        ),
        secondary=(
            RateLimitWindow(
                used_percent=secondary_percent,
                window_duration_minutes=10_080,
                resets_at=1_787_800_000,
            )
            if secondary_percent is not None
            else None
        ),
        account_usage_lifetime=12_345,
        account_usage_daily_delta=321,
    )


def make_record(
    *,
    task_id: str = "task-1",
    arm: str = "S1",
    role: str = "single_judge",
    attempts: int = 1,
    status: CallStatus = CallStatus.COMPLETED,
    transport_status: TransportStatus = TransportStatus.SUCCEEDED,
) -> ExperimentCallRecord:
    return record_from_model_usage(
        ModelUsage(
            model_id="gpt-5.6-sol-effective",
            effort="medium",
            input_tokens=100,
            output_tokens=30,
            thinking_tokens=10,
            cache_read_input_tokens=40,
            cache_creation_input_tokens=5,
            latency_ms=250,
            finish_reason="completed",
            request_id=f"call-{task_id}-{attempts}",
            attempts=attempts,
        ),
        experiment_id="experiment-2026-08-21",
        stable_task_id=task_id,
        stage="dev",
        arm=arm,
        patient_id="synthetic-patient-1",
        trial_id="NCT-SYNTHETIC",
        pair_id="synthetic-patient-1/NCT-SYNTHETIC",
        criterion_id="criterion-1",
        role=role,
        requested_model="gpt-5.6-sol",
        requested_effort="medium",
        schema_status=(
            SchemaStatus.VALID
            if status is CallStatus.COMPLETED
            else SchemaStatus.INVALID
        ),
        transport_status=transport_status,
        status=status,
        rate_limit_before=make_snapshot(10),
        rate_limit_after=make_snapshot(12, secondary_percent=7),
        thread_id="thread-1",
        turn_id="turn-1",
        error_code="timeout" if status is CallStatus.FAILED else None,
    )


def test_factory_preserves_usage_model_and_subscription_fields() -> None:
    record = make_record()

    assert record.requested_model == "gpt-5.6-sol"
    assert record.effective_model == "gpt-5.6-sol-effective"
    assert record.requested_effort == "medium"
    assert record.effective_effort == "medium"
    assert record.tokens.model_dump() == {
        "input_tokens": 100,
        "output_tokens": 30,
        "reasoning_tokens": 10,
        "cache_read_tokens": 40,
        "cache_write_tokens": 5,
        "total_tokens": 130,
    }
    assert record.call_id == "call-task-1-1"
    assert record.rate_limit_after is not None
    assert record.rate_limit_after.secondary is not None
    assert record.rate_limit_after.account_usage_lifetime == 12_345
    assert record.rate_limit_after.account_usage_daily_delta == 321


def test_factory_accepts_equivalent_mapping_and_reported_total() -> None:
    record = record_from_model_usage(
        {
            "model_id": "gpt-effective",
            "effort": "high",
            "input_tokens": 8,
            "output_tokens": 3,
            "thinking_tokens": 4,
            "cache_read_input_tokens": 2,
            "cache_creation_input_tokens": 1,
            "total_tokens": 15,
            "latency_ms": 99,
            "attempts": 2,
        },
        experiment_id="exp",
        stable_task_id="stable",
        stage="sensitivity",
        arm="M2",
        patient_id="synthetic-patient",
        trial_id="NCT-SYNTHETIC",
        pair_id="pair",
        criterion_id=None,
        role="reviewer",
        requested_model="gpt-requested",
        requested_effort="high",
        schema_status="repaired",
    )

    assert record.tokens.total_tokens == 15
    assert record.tokens.reasoning_tokens == 4
    assert record.attempts == 2


def test_pause_decision_uses_most_consumed_window_at_eighty_percent() -> None:
    below = decide_subscription_pause(make_snapshot(79.9, secondary_percent=20))
    at_limit = decide_subscription_pause(make_snapshot(10, secondary_percent=80))

    assert below.pause is False
    assert below.reason == "below_threshold"
    assert at_limit.pause is True
    assert at_limit.observed_percent == 80
    assert at_limit.limiting_window == "secondary"
    assert at_limit.reason == "threshold_reached"


def test_failures_are_appended_and_completion_is_restart_safe(tmp_path: Path) -> None:
    journal = tmp_path / "calls.jsonl"
    summary_path = tmp_path / "summary.json"
    tracker = ExperimentTracker(journal, summary_path)

    failed = make_record(
        attempts=1,
        status=CallStatus.FAILED,
        transport_status=TransportStatus.TIMEOUT,
    )
    completed = make_record(attempts=2)

    assert tracker.append(failed) is True
    assert tracker.should_skip("task-1", experiment_id="experiment-2026-08-21") is False
    assert tracker.append(completed) is True
    assert tracker.should_skip("task-1", experiment_id="experiment-2026-08-21") is True

    lines = journal.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["status"] == "failed"
    assert json.loads(lines[0])["attempts"] == 1
    assert json.loads(lines[1])["status"] == "completed"
    assert json.loads(lines[1])["attempts"] == 2

    restarted = ExperimentTracker(journal, summary_path)
    assert restarted.should_skip(
        "task-1", experiment_id="experiment-2026-08-21"
    )
    assert restarted.append(make_record(attempts=3)) is False
    assert len(journal.read_text(encoding="utf-8").splitlines()) == 2

    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    key = "experiment-2026-08-21/task-1"
    assert summary["completed_task_ids"] == [key]
    assert summary["max_attempts_by_task"][key] == 2
    assert summary["latest_status_by_task"][key] == "completed"
    assert not list(tmp_path.glob(".summary.json.*.tmp"))


def test_summary_reconciles_arm_stage_role_and_token_totals() -> None:
    records = [
        make_record(task_id="s1", arm="S1", role="single_judge"),
        make_record(task_id="m1", arm="M1", role="matcher"),
        make_record(task_id="m2", arm="M2", role="reviewer"),
    ]
    summary = build_experiment_summary(records, generated_at=NOW)

    assert summary.record_count == 3
    assert summary.completed_task_count == 3
    assert summary.totals.input_tokens == 300
    assert summary.totals.output_tokens == 90
    assert summary.totals.reasoning_tokens == 30
    assert summary.totals.cache_read_tokens == 120
    assert summary.totals.cache_write_tokens == 15
    assert summary.totals.total_tokens == 390
    assert summary.by_arm["S1"].total_tokens == 130
    assert summary.by_stage["dev"].record_count == 3
    assert summary.by_role["reviewer"].record_count == 1
    assert summary.token_reconciliation_ok is True


def test_record_contract_rejects_prompt_or_raw_content_fields() -> None:
    payload = make_record().model_dump()
    payload["prompt"] = "do not persist this"

    with pytest.raises(ValidationError):
        ExperimentCallRecord.model_validate(payload)


def test_corrupt_restart_journal_is_not_silently_ignored(tmp_path: Path) -> None:
    journal = tmp_path / "calls.jsonl"
    journal.write_text("{not-json}\n", encoding="utf-8")

    with pytest.raises(ValueError, match="line 1"):
        ExperimentTracker(journal)
