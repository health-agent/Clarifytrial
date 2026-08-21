from __future__ import annotations

import json
import urllib.error
from dataclasses import asdict
from typing import Any, Mapping

import pytest
from pydantic import BaseModel, ConfigDict

from clarifytrial.llm.anthropic import (
    ANTHROPIC_MESSAGES_URL,
    AnthropicAdapterError,
    AnthropicAPIError,
    AnthropicResponseError,
    AnthropicStructuredModel,
    AnthropicTransportError,
    HttpResponse,
    transform_json_schema,
)
from clarifytrial.llm.base import ModelCall


class ExampleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str


class FakeTransport:
    def __init__(self, *outcomes: HttpResponse | BaseException) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def send(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HttpResponse:
        self.calls.append(
            {
                "url": url,
                "headers": dict(headers),
                "body": body,
                "timeout_seconds": timeout_seconds,
            }
        )
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _response(
    *,
    status_code: int = 200,
    text: str = '{"value":"ok"}',
    headers: Mapping[str, str] | None = None,
    usage: Mapping[str, Any] | None = None,
) -> HttpResponse:
    payload = {
        "model": "claude-sonnet-5-20260801",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn",
        "usage": dict(
            usage
            or {
                "input_tokens": 120,
                "output_tokens": 18,
                "cache_creation_input_tokens": 40,
                "cache_read_input_tokens": 30,
                "thinking_tokens": 7,
            }
        ),
    }
    return HttpResponse(
        status_code=status_code,
        headers=dict(headers or {"request-id": "req-test-1"}),
        body=json.dumps(payload).encode("utf-8"),
    )


def _call() -> ModelCall[ExampleResponse]:
    return ModelCall(
        role="matcher_judge",
        prompt_id="prompts/matcher_judge.md",
        payload={"case_id": "synthetic-case-1", "value": "inspect"},
        response_model=ExampleResponse,
    )


def test_request_uses_adaptive_thinking_medium_effort_and_json_schema() -> None:
    secret = "sk-ant-test-secret"
    transport = FakeTransport(_response())
    ticks = iter([10.0, 10.125])
    model = AnthropicStructuredModel(
        api_key=secret,
        prompt_loader=lambda prompt_id: f"system prompt: {prompt_id}",
        transport=transport,
        clock=lambda: next(ticks),
    )

    response, usage = model.complete(_call())

    assert response == ExampleResponse(value="ok")
    assert len(transport.calls) == 1
    request = transport.calls[0]
    assert request["url"] == ANTHROPIC_MESSAGES_URL
    assert request["headers"]["x-api-key"] == secret
    assert request["headers"]["anthropic-version"] == "2023-06-01"
    assert secret.encode() not in request["body"]

    body = json.loads(request["body"])
    assert body["model"] == "claude-sonnet-5"
    assert body["thinking"] == {"type": "adaptive"}
    assert body["output_config"]["effort"] == "medium"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["output_config"]["format"]["schema"] == transform_json_schema(
        ExampleResponse.model_json_schema()
    )
    assert json.loads(body["messages"][0]["content"]) == _call().payload
    assert body["system"] == "system prompt: prompts/matcher_judge.md"
    assert {"temperature", "top_p", "top_k"}.isdisjoint(body)

    assert usage.model_id == "claude-sonnet-5-20260801"
    assert usage.effort == "medium"
    assert usage.input_tokens == 120
    assert usage.output_tokens == 18
    assert usage.cache_creation_input_tokens == 40
    assert usage.cache_read_input_tokens == 30
    assert usage.thinking_tokens == 7
    assert usage.latency_ms == 125
    assert usage.finish_reason == "end_turn"
    assert usage.request_id == "req-test-1"
    assert usage.attempts == 1
    assert secret not in json.dumps(asdict(usage))


def test_schema_transform_removes_constraints_rejected_by_raw_api() -> None:
    transformed = transform_json_schema(
        {
            "type": "object",
            "properties": {
                "count": {"type": "integer", "minimum": 0},
                "name": {"type": "string", "minLength": 1},
            },
        }
    )

    assert transformed == {
        "type": "object",
        "properties": {
            "count": {"type": "integer"},
            "name": {"type": "string"},
        },
        "additionalProperties": False,
    }


def test_429_and_5xx_are_retried_with_bounded_backoff() -> None:
    transport = FakeTransport(
        _response(status_code=429),
        _response(status_code=503),
        _response(headers={"request-id": "req-third"}),
    )
    delays: list[float] = []
    model = AnthropicStructuredModel(
        api_key="test-key",
        prompt_loader=lambda _: "prompt",
        transport=transport,
        max_retries=2,
        retry_delay_seconds=0.25,
        sleep=delays.append,
    )

    _, usage = model.complete(_call())

    assert len(transport.calls) == 3
    assert delays == [0.25, 0.5]
    assert usage.attempts == 3
    assert usage.request_id == "req-third"


def test_temporary_network_error_is_retried() -> None:
    transport = FakeTransport(
        urllib.error.URLError("temporary connection failure"),
        _response(),
    )
    model = AnthropicStructuredModel(
        api_key="test-key",
        prompt_loader=lambda _: "prompt",
        transport=transport,
        max_retries=1,
        retry_delay_seconds=0,
        sleep=lambda _: None,
    )

    _, usage = model.complete(_call())

    assert len(transport.calls) == 2
    assert usage.attempts == 2


def test_configured_model_id_is_used_when_response_omits_model() -> None:
    response = _response()
    payload = json.loads(response.body)
    del payload["model"]
    transport = FakeTransport(
        HttpResponse(
            status_code=response.status_code,
            headers=response.headers,
            body=json.dumps(payload).encode("utf-8"),
        )
    )
    model = AnthropicStructuredModel(
        api_key="test-key",
        prompt_loader=lambda _: "prompt",
        transport=transport,
    )

    _, usage = model.complete(_call())

    assert usage.model_id == "claude-sonnet-5"


def test_non_retryable_http_error_does_not_expose_key_or_response_body() -> None:
    secret = "sk-ant-never-log-this"
    failure = HttpResponse(
        status_code=401,
        headers={"request-id": f"request-{secret}"},
        body=f'{{"error":"bad key {secret}"}}'.encode(),
    )
    transport = FakeTransport(failure)
    model = AnthropicStructuredModel(
        api_key=secret,
        prompt_loader=lambda _: "prompt",
        transport=transport,
    )

    with pytest.raises(AnthropicAPIError) as captured:
        model.complete(_call())

    assert len(transport.calls) == 1
    assert "HTTP 401" in str(captured.value)
    assert secret not in str(captured.value)
    assert captured.value.__cause__ is None


def test_exhausted_network_retry_sanitizes_transport_exception() -> None:
    secret = "sk-ant-never-log-this"
    transport = FakeTransport(
        urllib.error.URLError(f"transport accidentally included {secret}"),
        urllib.error.URLError(f"transport accidentally included {secret}"),
    )
    model = AnthropicStructuredModel(
        api_key=secret,
        prompt_loader=lambda _: "prompt",
        transport=transport,
        max_retries=1,
        retry_delay_seconds=0,
        sleep=lambda _: None,
    )

    with pytest.raises(AnthropicTransportError) as captured:
        model.complete(_call())

    assert "after 2 attempts" in str(captured.value)
    assert secret not in str(captured.value)
    assert captured.value.__cause__ is None


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"unexpected":"field"}',
    ],
)
def test_invalid_structured_text_is_rejected_without_echoing_it(text: str) -> None:
    transport = FakeTransport(_response(text=text))
    model = AnthropicStructuredModel(
        api_key="test-key",
        prompt_loader=lambda _: "prompt",
        transport=transport,
    )

    with pytest.raises(AnthropicResponseError) as captured:
        model.complete(_call())

    assert "contract validation" in str(captured.value)
    assert text not in str(captured.value)


def test_api_key_can_come_from_environment_without_entering_the_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret = "sk-ant-from-environment"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    transport = FakeTransport(_response())
    model = AnthropicStructuredModel(
        prompt_loader=lambda _: "prompt",
        transport=transport,
    )

    model.complete(_call())

    assert transport.calls[0]["headers"]["x-api-key"] == secret
    assert secret.encode() not in transport.calls[0]["body"]


def test_key_accidentally_present_in_payload_is_blocked_before_transport() -> None:
    secret = "sk-ant-never-send-in-body"
    transport = FakeTransport(_response())
    model = AnthropicStructuredModel(
        api_key=secret,
        prompt_loader=lambda _: "prompt",
        transport=transport,
    )
    call = ModelCall(
        role="matcher_judge",
        prompt_id="prompts/matcher_judge.md",
        payload={"accidental_secret": secret},
        response_model=ExampleResponse,
    )

    with pytest.raises(AnthropicAdapterError) as captured:
        model.complete(call)

    assert transport.calls == []
    assert secret not in str(captured.value)


def test_key_echoed_in_structured_text_is_rejected_before_return() -> None:
    secret = "sk-ant-never-return-this"
    transport = FakeTransport(_response(text=json.dumps({"value": secret})))
    model = AnthropicStructuredModel(
        api_key=secret,
        prompt_loader=lambda _: "prompt",
        transport=transport,
    )

    with pytest.raises(AnthropicResponseError) as captured:
        model.complete(_call())

    assert secret not in str(captured.value)
