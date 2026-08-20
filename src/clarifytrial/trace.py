"""Inspectable JSONL execution traces.

Trace events contain identifiers and structured summaries, not hidden patient
facts, API keys, or a model's private chain of thought.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field

from .llm.base import ModelUsage


class TraceEvent(BaseModel):
    """One observable transition in an episode."""

    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=1)
    case_id: str
    cycle: int = Field(ge=0)
    actor: str
    event: str
    input_refs: list[str] = Field(default_factory=list)
    output: dict[str, Any] = Field(default_factory=dict)
    usage: dict[str, Any] | None = None


class TraceRecorder:
    """Collect events in memory and optionally write newline-delimited JSON."""

    def __init__(self, case_id: str) -> None:
        self.case_id = case_id
        self.events: list[TraceEvent] = []

    def record(
        self,
        *,
        cycle: int,
        actor: str,
        event: str,
        input_refs: list[str] | None = None,
        output: Mapping[str, Any] | None = None,
        usage: ModelUsage | None = None,
    ) -> TraceEvent:
        item = TraceEvent(
            sequence=len(self.events) + 1,
            case_id=self.case_id,
            cycle=cycle,
            actor=actor,
            event=event,
            input_refs=list(input_refs or []),
            output=dict(output or {}),
            usage=None if usage is None else asdict(usage),
        )
        self.events.append(item)
        return item

    def write_jsonl(self, path: str | Path) -> Path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        with destination.open("w", encoding="utf-8", newline="\n") as stream:
            for event in self.events:
                stream.write(json.dumps(event.model_dump(mode="json"), ensure_ascii=False))
                stream.write("\n")
        return destination
