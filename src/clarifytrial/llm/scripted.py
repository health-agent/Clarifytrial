"""Deterministic structured model used by tests and dry runs."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping
from typing import Any

from pydantic import BaseModel

from .base import ModelCall, ModelUsage, ResponseT


ScriptHandler = Callable[[Mapping[str, Any]], BaseModel | Mapping[str, Any]]


class ScriptedStructuredModel:
    """Route roles to local callables without contacting an external API."""

    def __init__(self, handlers: Mapping[str, ScriptHandler]) -> None:
        self._handlers = dict(handlers)
        self.call_count: Counter[str] = Counter()

    def complete(self, call: ModelCall[ResponseT]) -> tuple[ResponseT, ModelUsage]:
        try:
            handler = self._handlers[call.role]
        except KeyError as exc:
            raise KeyError(f"No scripted response for role {call.role!r}") from exc

        self.call_count[call.role] += 1
        raw = handler(call.payload)
        if isinstance(raw, call.response_model):
            response = raw
        elif isinstance(raw, BaseModel):
            response = call.response_model.model_validate(raw.model_dump(mode="json"))
        else:
            response = call.response_model.model_validate(raw)

        usage = ModelUsage(
            model_id="scripted-local",
            effort=None,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            finish_reason="stop",
        )
        return response, usage
