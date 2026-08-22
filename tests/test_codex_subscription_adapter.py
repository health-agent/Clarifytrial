from __future__ import annotations

import json
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
from pydantic import BaseModel, ConfigDict

from clarifytrial.llm.base import ModelCall
from clarifytrial.llm.codex_subscription import (
    DEFAULT_CODEX_EFFORT,
    DEFAULT_CODEX_MODEL,
    CodexRuntimeMetadata,
    CodexSubscriptionAdapterError,
    CodexSubscriptionClosedError,
    CodexSubscriptionModelPool,
    CodexSubscriptionResponseError,
    CodexSubscriptionStructuredModel,
    CodexSubscriptionTimeoutError,
    CodexSubscriptionToolUseError,
    CodexSubscriptionTransportError,
    CodexTokenUsage,
    _HARDENED_CONFIG_OVERRIDES,
    _WEB_REVIEW_CONFIG_OVERRIDES,
    _RunRequest,
    _SdkCodexSession,
    _SessionFailure,
    _TurnRecord,
    _strict_output_schema,
)


class ExampleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


class OptionalFieldResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[str] = []
    note: str | None = None


def test_strict_schema_requires_every_property_and_removes_defaults() -> None:
    schema = _strict_output_schema(OptionalFieldResponse.model_json_schema())

    assert schema["required"] == ["items", "note"]
    assert schema["additionalProperties"] is False
    assert "default" not in json.dumps(schema)


def _call(payload: Mapping[str, Any] | None = None) -> ModelCall[ExampleResponse]:
    return ModelCall(
        role="matcher_judge",
        prompt_id="prompts/matcher_judge.md",
        payload=dict(payload or {"case_id": "synthetic-1", "value": "inspect"}),
        response_model=ExampleResponse,
    )


class FakeSession:
    def __init__(self, outcomes: list[Any] | None = None) -> None:
        self.metadata = CodexRuntimeMetadata(
            sdk_version="0.147.0",
            runtime_name="codex-cli",
            runtime_version="0.147.0",
        )
        self.outcomes = list(outcomes or [])
        self.requests: list[_RunRequest] = []
        self.closed = False
        self._counter = 0

    def run(self, request: _RunRequest) -> _TurnRecord:
        self.requests.append(request)
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, BaseException):
                raise outcome
            return outcome
        self._counter += 1
        return _TurnRecord(
            thread_id=f"thread-{self._counter}",
            turn_id=f"turn-{self._counter}",
            final_response='{"value":"ok"}',
            status="completed",
            effective_model=DEFAULT_CODEX_MODEL,
            effective_effort=DEFAULT_CODEX_EFFORT,
            usage=CodexTokenUsage(
                input_tokens=101,
                output_tokens=11,
                reasoning_output_tokens=7,
                cached_input_tokens=13,
                cache_write_input_tokens=17,
                total_tokens=119,
            ),
        )

    def account_read(self) -> Mapping[str, Any]:
        return {
            "account": {
                "type": "chatgpt",
                "planType": "pro",
                "email": "private@example.test",
            },
            "requiresOpenaiAuth": True,
        }

    def model_list(self) -> Mapping[str, Any]:
        return {"data": [{"id": DEFAULT_CODEX_MODEL, "hidden": False}]}

    def rate_limits_read(self) -> Mapping[str, Any]:
        return {"rateLimits": {"limitId": "codex", "primary": {"usedPercent": 5}}}

    def account_usage_read(self) -> Mapping[str, Any]:
        return {"summary": {"lifetimeTokens": 123}, "dailyUsageBuckets": None}

    def close(self) -> None:
        self.closed = True


def test_reuses_session_but_starts_fresh_thread_and_returns_full_usage() -> None:
    session = FakeSession()
    factory_calls: list[str] = []
    ticks = iter([10.0, 10.125, 20.0, 20.250])
    model = CodexSubscriptionStructuredModel(
        prompt_loader=lambda prompt_id: f"prompt:{prompt_id}",
        session_factory=lambda cwd: factory_calls.append(cwd) or session,
        clock=lambda: next(ticks),
        request_id_factory=iter(["request-1", "request-2"]).__next__,
    )

    first, first_usage = model.complete(_call())
    second, second_usage = model.complete(_call({"case_id": "synthetic-2"}))

    assert first == second == ExampleResponse(value="ok")
    assert len(factory_calls) == 1
    assert len(session.requests) == 2
    assert first_usage.thread_id == "thread-1"
    assert second_usage.thread_id == "thread-2"
    assert first_usage.turn_id == "turn-1"
    assert first_usage.request_id == "request-1"
    assert first_usage.model_id == DEFAULT_CODEX_MODEL
    assert first_usage.effort == DEFAULT_CODEX_EFFORT
    assert first_usage.input_tokens == 101
    assert first_usage.output_tokens == 11
    assert first_usage.thinking_tokens == 7
    assert first_usage.cache_read_input_tokens == 13
    assert first_usage.cache_creation_input_tokens == 17
    assert first_usage.total_tokens == 119
    assert first_usage.latency_ms == 125
    assert first_usage.sdk_version == "0.147.0"
    assert first_usage.runtime_version == "0.147.0"
    assert first_usage.tool_events_audited is True
    request = session.requests[0]
    assert request.model == DEFAULT_CODEX_MODEL
    assert request.effort == DEFAULT_CODEX_EFFORT
    assert request.prompt == "prompt:prompts/matcher_judge.md"
    assert request.output_schema == _strict_output_schema(
        ExampleResponse.model_json_schema()
    )
    assert json.loads(request.payload_text)["payload"]["case_id"] == "synthetic-1"
    assert Path(request.cwd).is_dir()
    assert list(Path(request.cwd).iterdir()) == []
    assert not any((parent / ".git").exists() for parent in Path(request.cwd).parents)


def test_accepts_explicit_model_and_maximum_reasoning_effort() -> None:
    session = FakeSession(
        outcomes=[
            _TurnRecord(
                thread_id="thread-max",
                turn_id="turn-max",
                final_response='{"value":"ok"}',
                status="completed",
                effective_model="gpt-5.6-sol",
                effective_effort="max",
            )
        ]
    )
    model = CodexSubscriptionStructuredModel(
        model_id="gpt-5.6-sol",
        effort="max",
        prompt_loader=lambda _: "prompt",
        session_factory=lambda _: session,
    )

    _, usage = model.complete(_call())

    assert session.requests[0].model == "gpt-5.6-sol"
    assert session.requests[0].effort == "max"
    assert usage.requested_model_id == "gpt-5.6-sol"
    assert usage.requested_effort == "max"


def test_rejects_unknown_reasoning_effort() -> None:
    with pytest.raises(ValueError, match="effort must be one of"):
        CodexSubscriptionStructuredModel(effort="ultra")


def test_only_transient_failure_is_retried() -> None:
    transient = FakeSession(
        outcomes=[_SessionFailure("overload", transient=True), FakeSession().run(_dummy_request())]
    )
    delays: list[float] = []
    model = CodexSubscriptionStructuredModel(
        prompt_loader=lambda _: "prompt",
        session_factory=lambda _: transient,
        retry_delay_seconds=0.25,
        sleep=delays.append,
    )
    _, usage = model.complete(_call())
    assert usage.attempts == 2
    assert delays == [0.25]

    permanent = FakeSession(outcomes=[_SessionFailure("bad_request", transient=False)])
    rejected = CodexSubscriptionStructuredModel(
        prompt_loader=lambda _: "prompt",
        session_factory=lambda _: permanent,
        max_retries=2,
        sleep=lambda _: None,
    )
    with pytest.raises(CodexSubscriptionTransportError):
        rejected.complete(_call())
    assert len(permanent.requests) == 1


def test_tool_event_or_unaudited_result_fails_closed() -> None:
    record = FakeSession().run(_dummy_request())
    unsafe = _TurnRecord(
        **{**asdict(record), "forbidden_item_types": ("commandExecution",)}
    )
    model = CodexSubscriptionStructuredModel(
        prompt_loader=lambda _: "prompt",
        session_factory=lambda _: FakeSession([unsafe]),
    )
    with pytest.raises(CodexSubscriptionToolUseError):
        model.complete(_call())


def test_web_review_usage_retains_only_audited_search_events() -> None:
    record = FakeSession().run(_dummy_request())
    web_record = replace(
        record,
        web_search_events=(
            {
                "query": "chest tube routine clinical documentation",
                "action": {"type": "search"},
                "results": [
                    {"title": "Medical source", "url": "https://example.test"}
                ],
            },
        ),
    )
    model = CodexSubscriptionStructuredModel(
        web_search=True,
        prompt_loader=lambda _: "prompt",
        session_factory=lambda _: FakeSession([web_record]),
    )

    _, usage = model.complete(_call())

    assert usage.web_search_events[0]["query"] == (
        "chest tube routine clinical documentation"
    )
    assert 'web_search="live"' in _WEB_REVIEW_CONFIG_OVERRIDES
    assert "tools.web_search=true" in _WEB_REVIEW_CONFIG_OVERRIDES
    assert 'web_search="disabled"' not in _WEB_REVIEW_CONFIG_OVERRIDES


def test_invalid_output_and_credential_like_payload_are_not_exposed() -> None:
    raw = "raw-gold-must-not-leak"
    invalid = _TurnRecord(
        thread_id="thread",
        turn_id="turn",
        final_response=raw,
        status="completed",
        effective_model=DEFAULT_CODEX_MODEL,
        effective_effort=DEFAULT_CODEX_EFFORT,
    )
    model = CodexSubscriptionStructuredModel(
        prompt_loader=lambda _: "private prompt",
        session_factory=lambda _: FakeSession([invalid]),
    )
    with pytest.raises(CodexSubscriptionResponseError) as captured:
        model.complete(_call())
    assert raw not in str(captured.value)
    assert "private prompt" not in str(captured.value)

    untouched = FakeSession()
    blocked = CodexSubscriptionStructuredModel(
        prompt_loader=lambda _: "prompt",
        session_factory=lambda _: untouched,
    )
    with pytest.raises(CodexSubscriptionAdapterError) as secret_error:
        blocked.complete(_call({"access_token": "never-send-this"}))
    assert untouched.requests == []
    assert "never-send-this" not in str(secret_error.value)


def test_account_models_rate_limits_and_usage_probes_reuse_session_and_redact_email() -> None:
    session = FakeSession()
    model = CodexSubscriptionStructuredModel(session_factory=lambda _: session)
    account = model.account_info()
    assert account["account"] == {"type": "chatgpt", "planType": "pro"}
    assert model.available_models()["data"][0]["id"] == DEFAULT_CODEX_MODEL
    assert model.rate_limits()["rateLimits"]["limitId"] == "codex"
    assert model.account_usage()["summary"]["lifetimeTokens"] == 123
    assert model.runtime_metadata().runtime_version == "0.147.0"


def test_close_context_manager_and_timeout_cleanup() -> None:
    session = FakeSession()
    with pytest.raises(RuntimeError):
        with CodexSubscriptionStructuredModel(session_factory=lambda _: session) as model:
            model.runtime_metadata()
            raise RuntimeError("body failed")
    assert session.closed is True
    with pytest.raises(CodexSubscriptionClosedError):
        model.runtime_metadata()

    class BlockingSession(FakeSession):
        def run(self, request: _RunRequest) -> _TurnRecord:
            self.requests.append(request)
            time.sleep(0.2)
            return super().run(request)

    blocking = BlockingSession()
    timed = CodexSubscriptionStructuredModel(
        prompt_loader=lambda _: "prompt",
        session_factory=lambda _: blocking,
        timeout_seconds=0.01,
        max_retries=0,
    )
    with pytest.raises(CodexSubscriptionTimeoutError):
        timed.complete(_call())
    assert blocking.closed is True


def test_pool_runs_three_independent_calls_concurrently_and_closes_workers() -> None:
    barrier = threading.Barrier(3, timeout=2)
    sessions: list[FakeSession] = []

    class ParallelSession(FakeSession):
        def __init__(self, worker_id: int) -> None:
            super().__init__()
            self.worker_id = worker_id

        def run(self, request: _RunRequest) -> _TurnRecord:
            self.requests.append(request)
            barrier.wait()
            return _TurnRecord(
                thread_id=f"thread-worker-{self.worker_id}",
                turn_id=f"turn-worker-{self.worker_id}",
                final_response=json.dumps({"value": f"worker-{self.worker_id}"}),
                status="completed",
                effective_model=DEFAULT_CODEX_MODEL,
                effective_effort=DEFAULT_CODEX_EFFORT,
                usage=CodexTokenUsage(input_tokens=self.worker_id, output_tokens=1),
            )

    def worker_factory() -> CodexSubscriptionStructuredModel:
        session = ParallelSession(len(sessions) + 1)
        sessions.append(session)
        return CodexSubscriptionStructuredModel(
            prompt_loader=lambda _: "prompt",
            session_factory=lambda _cwd, owned=session: owned,
        )

    pool = CodexSubscriptionModelPool(size=3, worker_factory=worker_factory)
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(pool.complete, _call({"case_id": index})) for index in range(3)]
        results = [future.result(timeout=3) for future in futures]

    assert pool.size == 3
    assert {response.value for response, _ in results} == {
        "worker-1",
        "worker-2",
        "worker-3",
    }
    assert {usage.thread_id for _, usage in results} == {
        "thread-worker-1",
        "thread-worker-2",
        "thread-worker-3",
    }
    assert all(usage.tool_events_audited for _, usage in results)
    assert sum(len(session.requests) for session in sessions) == 3
    assert pool.runtime_metadata().runtime_version == "0.147.0"
    assert pool.rate_limits()["rateLimits"]["limitId"] == "codex"
    assert pool.account_usage()["summary"]["lifetimeTokens"] == 123
    pool.close()
    assert all(session.closed for session in sessions)
    with pytest.raises(CodexSubscriptionClosedError):
        pool.complete(_call())


def test_pool_close_waits_for_active_call_then_closes_every_worker() -> None:
    started = threading.Event()
    release = threading.Event()
    session = FakeSession()
    original_run = session.run

    def slow_run(request: _RunRequest) -> _TurnRecord:
        started.set()
        assert release.wait(timeout=2)
        return original_run(request)

    session.run = slow_run  # type: ignore[method-assign]
    worker = CodexSubscriptionStructuredModel(
        prompt_loader=lambda _: "prompt",
        session_factory=lambda _: session,
    )
    pool = CodexSubscriptionModelPool(size=1, worker_factory=lambda: worker)

    with ThreadPoolExecutor(max_workers=2) as executor:
        call_future = executor.submit(pool.complete, _call())
        assert started.wait(timeout=1)
        close_future = executor.submit(pool.close)
        time.sleep(0.02)
        assert close_future.done() is False
        release.set()
        response, usage = call_future.result(timeout=2)
        close_future.result(timeout=2)

    assert response == ExampleResponse(value="ok")
    assert usage.thread_id == "thread-1"
    assert session.closed is True


def test_sdk_layer_hardens_thread_and_collects_event_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeHandle:
        id = "turn-sdk"

        def stream(self):
            return iter(
                [
                    SimpleNamespace(
                        method="item/completed",
                        payload={
                            "item": {
                                "type": "agentMessage",
                                "phase": "final_answer",
                                "text": '{"value":"sdk"}',
                            }
                        },
                    ),
                    SimpleNamespace(
                        method="thread/tokenUsage/updated",
                        payload={
                            "tokenUsage": {
                                "last": {
                                    "inputTokens": 10,
                                    "outputTokens": 3,
                                    "reasoningOutputTokens": 2,
                                    "cachedInputTokens": 4,
                                    "cacheWriteInputTokens": 5,
                                    "totalTokens": 15,
                                }
                            }
                        },
                    ),
                    SimpleNamespace(
                        method="turn/completed",
                        payload={"turn": {"status": "completed"}},
                    ),
                ]
            )

        def interrupt(self) -> None:
            captured["interrupted"] = True

    class FakeThread:
        id = "thread-sdk"

        def turn(self, payload: str, **kwargs: Any) -> FakeHandle:
            captured["turn_payload"] = payload
            captured["turn_kwargs"] = kwargs
            return FakeHandle()

    class FakeCodex:
        def __init__(self, config: Any) -> None:
            captured["config"] = config
            self.metadata = SimpleNamespace(
                serverInfo=SimpleNamespace(name="codex-cli", version="0.147.0"),
                userAgent="codex-cli/0.147.0",
                platformFamily="windows",
                platformOs="windows",
            )

        def thread_start(self, **kwargs: Any) -> FakeThread:
            captured["thread_kwargs"] = kwargs
            return FakeThread()

        def close(self) -> None:
            captured["closed"] = True

    fake_sdk = types.ModuleType("openai_codex")
    fake_sdk.__version__ = "0.147.0"
    fake_sdk.CodexConfig = lambda **kwargs: kwargs
    fake_sdk.Codex = FakeCodex
    fake_sdk.ApprovalMode = SimpleNamespace(deny_all="deny_all")
    fake_sdk.Sandbox = SimpleNamespace(read_only="read_only")
    fake_sdk.TransportClosedError = type("TransportClosedError", (Exception,), {})
    fake_sdk.is_retryable_error = lambda _: False
    monkeypatch.setitem(sys.modules, "openai_codex", fake_sdk)

    session = _SdkCodexSession(str(Path.cwd()))
    record = session.run(_dummy_request(cwd=str(Path.cwd())))
    assert captured["config"]["config_overrides"] == _HARDENED_CONFIG_OVERRIDES
    assert captured["thread_kwargs"]["ephemeral"] is True
    assert captured["thread_kwargs"]["approval_mode"] == "deny_all"
    assert captured["thread_kwargs"]["sandbox"] == "read_only"
    assert captured["thread_kwargs"]["model"] == DEFAULT_CODEX_MODEL
    assert captured["turn_kwargs"]["effort"] == DEFAULT_CODEX_EFFORT
    assert captured["turn_kwargs"]["output_schema"] == ExampleResponse.model_json_schema()
    assert record.thread_id == "thread-sdk"
    assert record.turn_id == "turn-sdk"
    assert record.final_response == '{"value":"sdk"}'
    assert record.usage == CodexTokenUsage(
        input_tokens=10,
        output_tokens=3,
        reasoning_output_tokens=2,
        cached_input_tokens=4,
        cache_write_input_tokens=5,
        total_tokens=15,
    )
    session.close()
    assert captured["closed"] is True


def _dummy_request(*, cwd: str = ".") -> _RunRequest:
    return _RunRequest(
        model=DEFAULT_CODEX_MODEL,
        effort=DEFAULT_CODEX_EFFORT,
        prompt="prompt",
        payload_text='{"payload":{}}',
        output_schema=ExampleResponse.model_json_schema(),
        cwd=cwd,
    )
