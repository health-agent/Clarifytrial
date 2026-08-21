"""Durable, content-free accounting for subscription-backed experiments.

The tracker intentionally stores identifiers, provider counters, execution
status, and rate-limit observations only.  It has no field for prompts,
patient text, gold labels, credentials, or arbitrary metadata.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from enum import Enum
import json
import os
from pathlib import Path
import tempfile
from threading import RLock
from typing import Any, Iterable, Mapping

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .llm.base import ModelUsage


class ExperimentStage(str, Enum):
    SMOKE = "smoke"
    DEV = "dev"
    MAIN = "main"
    OVERLAP = "overlap"
    SENSITIVITY = "sensitivity"


class ExperimentArm(str, Enum):
    S1 = "S1"
    M1 = "M1"
    M2 = "M2"


class SchemaStatus(str, Enum):
    VALID = "valid"
    REPAIRED = "repaired"
    INVALID = "invalid"
    NOT_APPLICABLE = "not_applicable"


class TransportStatus(str, Enum):
    SUCCEEDED = "succeeded"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    FAILED = "failed"
    NOT_ATTEMPTED = "not_attempted"


class CallStatus(str, Enum):
    STARTED = "started"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    SKIPPED = "skipped"


class TokenCounts(BaseModel):
    """Provider token counters preserved without imposing provider semantics."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_tokens: int = Field(default=0, ge=0)
    cache_read_tokens: int = Field(default=0, ge=0)
    cache_write_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)

class RateLimitWindow(BaseModel):
    """One App Server subscription usage window."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    used_percent: float = Field(ge=0, le=100)
    window_duration_minutes: int | None = Field(default=None, ge=0)
    resets_at: int | None = Field(
        default=None,
        ge=0,
        description="Provider-reported Unix timestamp, preserved without conversion.",
    )


class SubscriptionRateLimitSnapshot(BaseModel):
    """Rate-limit state read from Codex App Server before or after a call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    observed_at: datetime
    limit_id: str
    primary: RateLimitWindow | None = None
    secondary: RateLimitWindow | None = None
    account_usage_lifetime: float | None = Field(default=None, ge=0)
    account_usage_daily_delta: float | None = Field(default=None, ge=0)

    @field_validator("observed_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        return value

    @field_validator("limit_id")
    @classmethod
    def require_limit_id(cls, value: str) -> str:
        return _safe_identifier(value, "limit_id")


class PauseDecision(BaseModel):
    """Pure decision result for subscription-budget flow control."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    pause: bool
    threshold_percent: float = Field(ge=0, le=100)
    observed_percent: float | None = Field(default=None, ge=0, le=100)
    limiting_window: str | None = None
    reason: str


class ExperimentCallRecord(BaseModel):
    """One append-only call/progress record with no model or patient content."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = Field(default=1, ge=1)
    recorded_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    experiment_id: str
    stable_task_id: str
    stage: ExperimentStage
    arm: ExperimentArm
    patient_id: str
    trial_id: str
    pair_id: str
    criterion_id: str | None = None
    role: str
    requested_model: str
    requested_effort: str | None = None
    effective_model: str
    effective_effort: str | None = None
    tokens: TokenCounts = Field(default_factory=TokenCounts)
    latency_ms: int | None = Field(default=None, ge=0)
    attempts: int = Field(default=1, ge=1)
    schema_status: SchemaStatus
    transport_status: TransportStatus
    status: CallStatus
    rate_limit_before: SubscriptionRateLimitSnapshot | None = None
    rate_limit_after: SubscriptionRateLimitSnapshot | None = None
    call_id: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    finish_reason: str | None = None
    error_code: str | None = None

    @field_validator("recorded_at")
    @classmethod
    def require_aware_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("recorded_at must include a timezone")
        return value

    @field_validator(
        "experiment_id",
        "stable_task_id",
        "patient_id",
        "trial_id",
        "pair_id",
        "role",
        "requested_model",
        "effective_model",
        "call_id",
        "thread_id",
        "turn_id",
        "criterion_id",
        "error_code",
        mode="before",
    )
    @classmethod
    def validate_identifier(cls, value: str | None, info: Any) -> str | None:
        if value is None:
            return None
        return _safe_identifier(value, info.field_name)


class AggregateTotals(BaseModel):
    """Totals for one arm, stage, role, or the entire journal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    record_count: int = Field(ge=0)
    unique_task_count: int = Field(ge=0)
    status_counts: dict[str, int]
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    cache_read_tokens: int = Field(ge=0)
    cache_write_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    latency_ms: int = Field(ge=0)
    token_reconciliation_ok: bool


class ExperimentSummary(BaseModel):
    """Atomic restart/progress summary derived only from the JSONL journal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: int = 1
    generated_at: datetime
    record_count: int = Field(ge=0)
    unique_task_count: int = Field(ge=0)
    completed_task_count: int = Field(ge=0)
    completed_task_ids: list[str]
    latest_status_by_task: dict[str, str]
    max_attempts_by_task: dict[str, int]
    totals: AggregateTotals
    by_arm: dict[str, AggregateTotals]
    by_stage: dict[str, AggregateTotals]
    by_role: dict[str, AggregateTotals]
    token_reconciliation_ok: bool


def decide_subscription_pause(
    snapshot: SubscriptionRateLimitSnapshot | None,
    *,
    threshold_percent: float = 80.0,
) -> PauseDecision:
    """Return a deterministic pause decision from one immutable snapshot."""

    if not 0 <= threshold_percent <= 100:
        raise ValueError("threshold_percent must be between 0 and 100")
    if snapshot is None:
        return PauseDecision(
            pause=False,
            threshold_percent=threshold_percent,
            observed_percent=None,
            reason="no_rate_limit_snapshot",
        )

    windows = [
        ("primary", snapshot.primary),
        ("secondary", snapshot.secondary),
    ]
    present = [(name, window) for name, window in windows if window is not None]
    if not present:
        return PauseDecision(
            pause=False,
            threshold_percent=threshold_percent,
            observed_percent=None,
            reason="no_usage_window",
        )

    limiting_name, limiting_window = max(
        present,
        key=lambda item: item[1].used_percent,  # type: ignore[union-attr]
    )
    observed = limiting_window.used_percent  # type: ignore[union-attr]
    pause = observed >= threshold_percent
    return PauseDecision(
        pause=pause,
        threshold_percent=threshold_percent,
        observed_percent=observed,
        limiting_window=limiting_name,
        reason="threshold_reached" if pause else "below_threshold",
    )


def record_from_model_usage(
    usage: ModelUsage | Mapping[str, Any],
    *,
    experiment_id: str,
    stable_task_id: str,
    stage: ExperimentStage | str,
    arm: ExperimentArm | str,
    patient_id: str,
    trial_id: str,
    pair_id: str,
    role: str,
    requested_model: str,
    requested_effort: str | None,
    schema_status: SchemaStatus | str,
    transport_status: TransportStatus | str = TransportStatus.SUCCEEDED,
    status: CallStatus | str = CallStatus.COMPLETED,
    criterion_id: str | None = None,
    rate_limit_before: SubscriptionRateLimitSnapshot | None = None,
    rate_limit_after: SubscriptionRateLimitSnapshot | None = None,
    call_id: str | None = None,
    thread_id: str | None = None,
    turn_id: str | None = None,
    error_code: str | None = None,
) -> ExperimentCallRecord:
    """Convert current ``ModelUsage`` or an equivalent mapping into a record."""

    values = _usage_values(usage)
    input_tokens = _optional_counter(values.get("input_tokens"))
    output_tokens = _optional_counter(values.get("output_tokens"))
    effective_model = values.get("model_id") or requested_model
    effective_effort = values.get("effort")
    request_id = values.get("request_id")

    return ExperimentCallRecord(
        experiment_id=experiment_id,
        stable_task_id=stable_task_id,
        stage=stage,
        arm=arm,
        patient_id=patient_id,
        trial_id=trial_id,
        pair_id=pair_id,
        criterion_id=criterion_id,
        role=role,
        requested_model=requested_model,
        requested_effort=requested_effort,
        effective_model=str(effective_model),
        effective_effort=(
            str(effective_effort) if effective_effort is not None else None
        ),
        tokens=TokenCounts(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=_optional_counter(values.get("thinking_tokens")),
            cache_read_tokens=_optional_counter(
                values.get("cache_read_input_tokens")
            ),
            cache_write_tokens=_optional_counter(
                values.get("cache_creation_input_tokens")
            ),
            total_tokens=_optional_counter(
                values.get("total_tokens")
                if values.get("total_tokens") is not None
                else input_tokens + output_tokens
            ),
        ),
        latency_ms=values.get("latency_ms"),
        attempts=values.get("attempts") or 1,
        schema_status=schema_status,
        transport_status=transport_status,
        status=status,
        rate_limit_before=rate_limit_before,
        rate_limit_after=rate_limit_after,
        call_id=call_id or (str(request_id) if request_id is not None else None),
        thread_id=thread_id,
        turn_id=turn_id,
        finish_reason=values.get("finish_reason"),
        error_code=error_code,
    )


def build_experiment_summary(
    records: Iterable[ExperimentCallRecord],
    *,
    generated_at: datetime | None = None,
) -> ExperimentSummary:
    """Build all progress and grouped totals from append-only records."""

    rows = list(records)
    task_key = lambda row: f"{row.experiment_id}/{row.stable_task_id}"
    latest_status: dict[str, str] = {}
    max_attempts: dict[str, int] = {}
    completed: set[str] = set()
    for row in rows:
        key = task_key(row)
        latest_status[key] = row.status.value
        max_attempts[key] = max(max_attempts.get(key, 0), row.attempts)
        if row.status is CallStatus.COMPLETED:
            completed.add(key)

    totals = _aggregate(rows)
    by_arm = _group_aggregates(rows, lambda row: row.arm.value)
    by_stage = _group_aggregates(rows, lambda row: row.stage.value)
    by_role = _group_aggregates(rows, lambda row: row.role)
    return ExperimentSummary(
        generated_at=generated_at or datetime.now(timezone.utc),
        record_count=len(rows),
        unique_task_count=len({task_key(row) for row in rows}),
        completed_task_count=len(completed),
        completed_task_ids=sorted(completed),
        latest_status_by_task=latest_status,
        max_attempts_by_task=max_attempts,
        totals=totals,
        by_arm=by_arm,
        by_stage=by_stage,
        by_role=by_role,
        token_reconciliation_ok=all(
            _partition_reconciles(totals, groups)
            for groups in (by_arm, by_stage, by_role)
        ),
    )


class ExperimentTracker:
    """Append records durably and replace a derived summary atomically."""

    def __init__(
        self,
        jsonl_path: str | Path,
        summary_path: str | Path | None = None,
    ) -> None:
        self.jsonl_path = Path(jsonl_path)
        self.summary_path = (
            Path(summary_path)
            if summary_path is not None
            else self.jsonl_path.with_name("summary.json")
        )
        self._lock = RLock()
        self._records = self._read_records()
        self._completed = {
            (record.experiment_id, record.stable_task_id)
            for record in self._records
            if record.status is CallStatus.COMPLETED
        }
        self._write_summary_atomic(build_experiment_summary(self._records))

    @property
    def records(self) -> tuple[ExperimentCallRecord, ...]:
        return tuple(self._records)

    def should_skip(self, stable_task_id: str, *, experiment_id: str) -> bool:
        """Return whether this exact experiment task has already completed."""

        return (experiment_id, stable_task_id) in self._completed

    def append(self, record: ExperimentCallRecord) -> bool:
        """Append and summarize, or return ``False`` for an already-complete task."""

        with self._lock:
            key = (record.experiment_id, record.stable_task_id)
            if key in self._completed:
                return False

            self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
            serialized = record.model_dump_json() + "\n"
            with self.jsonl_path.open("a", encoding="utf-8", newline="") as handle:
                handle.write(serialized)
                handle.flush()
                os.fsync(handle.fileno())

            self._records.append(record)
            if record.status is CallStatus.COMPLETED:
                self._completed.add(key)
            self._write_summary_atomic(build_experiment_summary(self._records))
            return True

    def summary(self) -> ExperimentSummary:
        with self._lock:
            return build_experiment_summary(self._records)

    def _read_records(self) -> list[ExperimentCallRecord]:
        if not self.jsonl_path.exists():
            return []
        records: list[ExperimentCallRecord] = []
        with self.jsonl_path.open("r", encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                try:
                    records.append(ExperimentCallRecord.model_validate_json(line))
                except Exception as exc:
                    raise ValueError(
                        f"invalid experiment JSONL record at line {line_number}"
                    ) from exc
        return records

    def _write_summary_atomic(self, summary: ExperimentSummary) -> None:
        self.summary_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self.summary_path.parent,
                prefix=f".{self.summary_path.name}.",
                suffix=".tmp",
                delete=False,
            ) as handle:
                temporary_path = Path(handle.name)
                payload = summary.model_dump(mode="json")
                json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.summary_path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()


def _safe_identifier(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{field_name} must be a string")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{field_name} must not be empty")
    if any(character in stripped for character in ("\r", "\n", "\x00")):
        raise ValueError(f"{field_name} must not contain control line breaks")
    if len(stripped) > 512:
        raise ValueError(f"{field_name} must not exceed 512 characters")
    return stripped


def _optional_counter(value: Any) -> int:
    if value is None:
        return 0
    result = int(value)
    if result < 0:
        raise ValueError("token counters must be non-negative")
    return result


def _usage_values(usage: ModelUsage | Mapping[str, Any]) -> Mapping[str, Any]:
    if isinstance(usage, Mapping):
        return usage
    return {
        "model_id": usage.model_id,
        "effort": usage.effort,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_creation_input_tokens": usage.cache_creation_input_tokens,
        "cache_read_input_tokens": usage.cache_read_input_tokens,
        "thinking_tokens": usage.thinking_tokens,
        "latency_ms": usage.latency_ms,
        "finish_reason": usage.finish_reason,
        "request_id": usage.request_id,
        "attempts": usage.attempts,
    }


def _aggregate(records: Iterable[ExperimentCallRecord]) -> AggregateTotals:
    rows = list(records)
    input_tokens = sum(row.tokens.input_tokens for row in rows)
    output_tokens = sum(row.tokens.output_tokens for row in rows)
    total_tokens = sum(row.tokens.total_tokens for row in rows)
    return AggregateTotals(
        record_count=len(rows),
        unique_task_count=len(
            {(row.experiment_id, row.stable_task_id) for row in rows}
        ),
        status_counts=dict(sorted(Counter(row.status.value for row in rows).items())),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        reasoning_tokens=sum(row.tokens.reasoning_tokens for row in rows),
        cache_read_tokens=sum(row.tokens.cache_read_tokens for row in rows),
        cache_write_tokens=sum(row.tokens.cache_write_tokens for row in rows),
        total_tokens=total_tokens,
        latency_ms=sum(row.latency_ms or 0 for row in rows),
        token_reconciliation_ok=True,
    )


def _group_aggregates(
    records: Iterable[ExperimentCallRecord],
    key: Any,
) -> dict[str, AggregateTotals]:
    groups: dict[str, list[ExperimentCallRecord]] = {}
    for record in records:
        groups.setdefault(key(record), []).append(record)
    return {name: _aggregate(groups[name]) for name in sorted(groups)}


def _partition_reconciles(
    totals: AggregateTotals,
    groups: Mapping[str, AggregateTotals],
) -> bool:
    fields = (
        "input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "cache_read_tokens",
        "cache_write_tokens",
        "total_tokens",
    )
    return all(
        getattr(totals, field_name)
        == sum(getattr(group, field_name) for group in groups.values())
        for field_name in fields
    )


__all__ = [
    "AggregateTotals",
    "CallStatus",
    "ExperimentArm",
    "ExperimentCallRecord",
    "ExperimentStage",
    "ExperimentSummary",
    "ExperimentTracker",
    "PauseDecision",
    "RateLimitWindow",
    "SchemaStatus",
    "SubscriptionRateLimitSnapshot",
    "TokenCounts",
    "TransportStatus",
    "build_experiment_summary",
    "decide_subscription_pause",
    "record_from_model_usage",
]
