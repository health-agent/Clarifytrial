"""Small, provider-neutral boundary for structured model calls.

Agent and evaluation code must not import a vendor SDK. A provider adapter may
translate :class:`ModelCall` into its API request, but the response must validate
against the requested Pydantic model before it enters the workflow.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Mapping, Protocol, TypeVar

from pydantic import BaseModel


ResponseT = TypeVar("ResponseT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ModelUsage:
    """Usage data reported by the provider, when available."""

    model_id: str
    effort: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_creation_input_tokens: int | None = None
    cache_read_input_tokens: int | None = None
    thinking_tokens: int | None = None
    latency_ms: int | None = None
    finish_reason: str | None = None
    request_id: str | None = None
    attempts: int = 1


@dataclass(frozen=True, slots=True)
class ModelCall(Generic[ResponseT]):
    """A fully specified structured model request."""

    role: str
    prompt_id: str
    payload: Mapping[str, Any]
    response_model: type[ResponseT]


class StructuredModel(Protocol):
    """Interface implemented by vendor adapters and deterministic test models."""

    def complete(self, call: ModelCall[ResponseT]) -> tuple[ResponseT, ModelUsage]:
        """Return one validated response and its provider-reported usage."""
