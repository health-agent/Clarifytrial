"""Anthropic Messages API adapter using only Python's standard HTTP library."""

from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol, cast

from pydantic import ValidationError

from .base import ModelCall, ModelUsage, ResponseT
from .prompts import PromptLoader, repository_prompt_loader


ANTHROPIC_MESSAGES_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

_UNSUPPORTED_SCHEMA_KEYS = {
    "default",
    "examples",
    "exclusiveMaximum",
    "exclusiveMinimum",
    "format",
    "maxItems",
    "maxLength",
    "maxProperties",
    "maximum",
    "minItems",
    "minLength",
    "minProperties",
    "minimum",
    "multipleOf",
    "pattern",
    "title",
    "uniqueItems",
}


def transform_json_schema(value: Any) -> Any:
    """Remove constraints unsupported by Claude grammar; Pydantic checks them later."""

    if isinstance(value, list):
        return [transform_json_schema(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    transformed = {
        key: transform_json_schema(item)
        for key, item in value.items()
        if key not in _UNSUPPORTED_SCHEMA_KEYS
    }
    if transformed.get("type") == "object":
        transformed["additionalProperties"] = False
    return transformed


class AnthropicAdapterError(RuntimeError):
    """Base error with messages that never include credentials or response text."""


class AnthropicAPIError(AnthropicAdapterError):
    """The API returned a non-retryable status or exhausted retry attempts."""


class AnthropicTransportError(AnthropicAdapterError):
    """The HTTP exchange failed before a usable response was received."""


class AnthropicResponseError(AnthropicAdapterError):
    """The API response could not satisfy the requested structured contract."""


@dataclass(frozen=True, slots=True)
class HttpResponse:
    """Minimal HTTP result shared by the real and test transports."""

    status_code: int
    headers: Mapping[str, str]
    body: bytes


class HttpTransport(Protocol):
    """Injectable transport so unit tests never contact the network."""

    def send(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HttpResponse:
        """POST one JSON request and return status, headers, and raw bytes."""


class UrllibHttpTransport:
    """Standard-library implementation of the Anthropic HTTP exchange."""

    def send(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HttpResponse:
        request = urllib.request.Request(
            url=url,
            data=body,
            headers=dict(headers),
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                return HttpResponse(
                    status_code=response.getcode(),
                    headers=dict(response.headers.items()),
                    body=response.read(),
                )
        except urllib.error.HTTPError as error:
            return HttpResponse(
                status_code=error.code,
                headers=dict(error.headers.items()) if error.headers else {},
                body=error.read(),
            )


class AnthropicStructuredModel:
    """Make isolated Claude calls and validate their JSON against Pydantic models."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model_id: str = "claude-sonnet-5",
        max_output_tokens: int = 4096,
        timeout_seconds: float = 120,
        max_retries: int = 2,
        retry_delay_seconds: float = 0.5,
        prompt_loader: PromptLoader | None = None,
        transport: HttpTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.perf_counter,
        endpoint: str = ANTHROPIC_MESSAGES_URL,
    ) -> None:
        resolved_key = api_key if api_key is not None else os.getenv("ANTHROPIC_API_KEY")
        if resolved_key is None or not resolved_key.strip():
            raise ValueError(
                "Anthropic API key is required via the constructor or "
                "ANTHROPIC_API_KEY"
            )
        if not model_id.strip():
            raise ValueError("model_id must not be empty")
        if max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must not be negative")

        self._api_key = resolved_key.strip()
        self._model_id = model_id
        self._max_output_tokens = max_output_tokens
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds
        self._prompt_loader = prompt_loader or repository_prompt_loader()
        self._transport = transport or UrllibHttpTransport()
        self._sleep = sleep
        self._clock = clock
        self._endpoint = endpoint

    def complete(self, call: ModelCall[ResponseT]) -> tuple[ResponseT, ModelUsage]:
        system_prompt = self._prompt_loader(call.prompt_id)
        request_body = self._request_body(call, system_prompt)
        encoded_body = json.dumps(
            request_body,
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        if self._api_key.encode("utf-8") in encoded_body:
            raise AnthropicAdapterError(
                "model request content contains credential material"
            )
        headers = {
            "content-type": "application/json",
            "anthropic-version": ANTHROPIC_VERSION,
            "x-api-key": self._api_key,
        }

        started = self._clock()
        response, attempts = self._send_with_retries(headers, encoded_body)
        response_payload = self._decode_response(response)
        typed_response = self._validate_structured_output(
            response_payload,
            call,
        )
        latency_ms = max(0, round((self._clock() - started) * 1000))
        usage = self._usage_from_response(
            response_payload,
            response.headers,
            latency_ms=latency_ms,
            attempts=attempts,
        )
        return typed_response, usage

    def _request_body(
        self,
        call: ModelCall[ResponseT],
        system_prompt: str,
    ) -> dict[str, Any]:
        try:
            payload_text = json.dumps(
                call.payload,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            raise AnthropicAdapterError(
                "model payload is not JSON serializable"
            ) from None

        return {
            "model": self._model_id,
            "max_tokens": self._max_output_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": payload_text}],
            "thinking": {"type": "adaptive"},
            "output_config": {
                "effort": "medium",
                "format": {
                    "type": "json_schema",
                    "schema": transform_json_schema(
                        call.response_model.model_json_schema()
                    ),
                },
            },
        }

    def _send_with_retries(
        self,
        headers: Mapping[str, str],
        body: bytes,
    ) -> tuple[HttpResponse, int]:
        for attempts in range(1, self._max_retries + 2):
            try:
                response = self._transport.send(
                    url=self._endpoint,
                    headers=headers,
                    body=body,
                    timeout_seconds=self._timeout_seconds,
                )
            except (
                urllib.error.URLError,
                TimeoutError,
                socket.timeout,
                ConnectionError,
                OSError,
            ):
                if attempts <= self._max_retries:
                    self._wait_before_retry(attempts)
                    continue
                raise AnthropicTransportError(
                    f"Anthropic request failed after {attempts} attempts"
                ) from None

            if 200 <= response.status_code < 300:
                return response, attempts
            if (
                response.status_code == 429 or 500 <= response.status_code <= 599
            ) and attempts <= self._max_retries:
                self._wait_before_retry(attempts)
                continue

            request_id = self._without_api_key(self._request_id(response.headers))
            suffix = "" if request_id is None else f" (request_id={request_id})"
            detail = self._safe_error_detail(response.body)
            if detail:
                suffix += f": {detail}"
            raise AnthropicAPIError(
                f"Anthropic API returned HTTP {response.status_code} "
                f"after {attempts} attempts{suffix}"
            ) from None

        raise AssertionError("retry loop ended without a response")

    def _wait_before_retry(self, failed_attempt: int) -> None:
        delay = self._retry_delay_seconds * (2 ** (failed_attempt - 1))
        self._sleep(delay)

    @staticmethod
    def _decode_response(response: HttpResponse) -> Mapping[str, Any]:
        try:
            payload = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise AnthropicResponseError(
                "Anthropic response was not a valid JSON object"
            ) from None
        if not isinstance(payload, Mapping):
            raise AnthropicResponseError(
                "Anthropic response was not a valid JSON object"
            )
        return cast(Mapping[str, Any], payload)

    def _validate_structured_output(
        self,
        response_payload: Mapping[str, Any],
        call: ModelCall[ResponseT],
    ) -> ResponseT:
        content = response_payload.get("content")
        if not isinstance(content, list):
            raise AnthropicResponseError(
                "Anthropic response did not contain structured text output"
            )
        text_blocks = [
            block.get("text")
            for block in content
            if isinstance(block, Mapping)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ]
        if not text_blocks:
            raise AnthropicResponseError(
                "Anthropic response did not contain structured text output"
            )
        structured_text = "".join(cast(list[str], text_blocks))
        if self._api_key in structured_text:
            raise AnthropicResponseError(
                "Anthropic structured output contained credential material"
            )
        try:
            raw_output = json.loads(structured_text)
            return call.response_model.model_validate(raw_output)
        except (json.JSONDecodeError, ValidationError):
            raise AnthropicResponseError(
                "Anthropic structured output failed contract validation"
            ) from None

    def _usage_from_response(
        self,
        response_payload: Mapping[str, Any],
        headers: Mapping[str, str],
        *,
        latency_ms: int,
        attempts: int,
    ) -> ModelUsage:
        raw_usage = response_payload.get("usage")
        usage = raw_usage if isinstance(raw_usage, Mapping) else {}
        thinking_tokens = self._optional_token_count(usage, "thinking_tokens")
        if thinking_tokens is None:
            details = usage.get("output_tokens_details")
            if isinstance(details, Mapping):
                thinking_tokens = self._optional_token_count(
                    details,
                    "thinking_tokens",
                )

        stop_reason = response_payload.get("stop_reason")
        finish_reason = stop_reason if isinstance(stop_reason, str) else None
        request_id = self._request_id(headers)
        response_model = response_payload.get("model")
        response_model_id = (
            self._without_api_key(response_model)
            if isinstance(response_model, str)
            else None
        )
        return ModelUsage(
            model_id=response_model_id or self._model_id,
            effort="medium",
            input_tokens=self._optional_token_count(usage, "input_tokens"),
            output_tokens=self._optional_token_count(usage, "output_tokens"),
            cache_creation_input_tokens=self._optional_token_count(
                usage,
                "cache_creation_input_tokens",
            ),
            cache_read_input_tokens=self._optional_token_count(
                usage,
                "cache_read_input_tokens",
            ),
            thinking_tokens=thinking_tokens,
            latency_ms=latency_ms,
            finish_reason=self._without_api_key(finish_reason),
            request_id=self._without_api_key(request_id),
            attempts=attempts,
        )

    @staticmethod
    def _optional_token_count(
        values: Mapping[str, Any],
        key: str,
    ) -> int | None:
        value = values.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise AnthropicResponseError(
                f"Anthropic usage field {key!r} was not a non-negative integer"
            )
        return value

    @staticmethod
    def _request_id(headers: Mapping[str, str]) -> str | None:
        normalized = {key.casefold(): value for key, value in headers.items()}
        return normalized.get("request-id") or normalized.get("x-request-id")

    def _without_api_key(self, value: str | None) -> str | None:
        if value is None or self._api_key in value:
            return None
        return value

    def _safe_error_detail(self, body: bytes) -> str | None:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, Mapping):
            return None
        error = payload.get("error")
        if not isinstance(error, Mapping):
            return None
        message = error.get("message")
        if not isinstance(message, str):
            return None
        cleaned = " ".join(message.split())[:500]
        return self._without_api_key(cleaned)
