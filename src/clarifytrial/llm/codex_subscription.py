"""Structured-model adapter backed by a reusable Codex App Server session.

The adapter reuses Codex's existing ChatGPT authentication, but each model call
uses a fresh ephemeral thread in an empty temporary workspace.  No provider
credentials are accepted by this module.
"""

from __future__ import annotations

import json
import queue
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol, cast

from pydantic import ValidationError

from .base import ModelCall, ModelUsage, ResponseT
from .prompts import PromptLoader, repository_prompt_loader


DEFAULT_CODEX_MODEL = "gpt-5.6-sol"
DEFAULT_CODEX_EFFORT = "medium"
ALLOWED_CODEX_EFFORTS = frozenset(
    {"none", "low", "medium", "high", "xhigh", "max"}
)
CODEX_SDK_REQUIREMENT = "openai-codex==0.147.0"

_BASE_INSTRUCTIONS = """You are a structured inference engine.
Return exactly one JSON value conforming to the supplied output schema.
Use only the developer instructions and the user payload supplied in this turn.
Do not call tools, inspect files, inspect environment variables, use the network,
start subagents, or retrieve any other context.
"""

_WEB_REVIEW_BASE_INSTRUCTIONS = """You are a structured clinical-research inference engine.
Return exactly one JSON value conforming to the supplied output schema.
Use only the developer instructions and the user payload supplied in this turn.
You may use web search only to verify general medical terminology, established
medical relationships, or ordinary documentation practice. Never search for a
patient sentence, patient or trial identifier, benchmark name, annotation ID,
or answer label. Do not call any other tool, inspect files, inspect environment
variables, start subagents, or retrieve private context.
"""

_HARDENED_CONFIG_OVERRIDES = (
    "features.shell_tool=false",
    "features.unified_exec=false",
    "features.multi_agent=false",
    "agents.enabled=false",
    "features.apps=false",
    "features.remote_plugin=false",
    "features.hooks=false",
    "features.memories=false",
    "memories.generate_memories=false",
    'web_search="disabled"',
    "tools.web_search=false",
    "tools.view_image=false",
    "mcp_servers={}",
    'history.persistence="none"',
    'shell_environment_policy.inherit="none"',
)

_WEB_REVIEW_CONFIG_OVERRIDES = tuple(
    item
    for item in _HARDENED_CONFIG_OVERRIDES
    if not item.startswith("web_search=") and not item.startswith("tools.web_search=")
) + ('web_search="live"', "tools.web_search=true")

_ALLOWED_ITEM_TYPES = frozenset({"userMessage", "agentMessage", "reasoning"})
_WEB_REVIEW_ALLOWED_ITEM_TYPES = _ALLOWED_ITEM_TYPES | {"webSearch"}
_SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "authorization",
        "access_token",
        "refresh_token",
        "password",
        "secret",
        "credential",
        "credentials",
    }
)


def _strict_output_schema(value: Any) -> Any:
    """Make Pydantic JSON Schema compatible with strict structured output."""

    if isinstance(value, list):
        return [_strict_output_schema(item) for item in value]
    if not isinstance(value, Mapping):
        return value
    transformed = {
        str(key): _strict_output_schema(item)
        for key, item in value.items()
        if key != "default"
    }
    properties = transformed.get("properties")
    if isinstance(properties, Mapping):
        transformed["required"] = list(properties)
        transformed["additionalProperties"] = False
    return transformed


class CodexSubscriptionAdapterError(RuntimeError):
    """Base error whose message never contains prompts, payloads, or credentials."""


class CodexSubscriptionUnavailableError(CodexSubscriptionAdapterError):
    """The optional Codex SDK or its pinned runtime could not be started."""


class CodexSubscriptionTransportError(CodexSubscriptionAdapterError):
    """The App Server exchange failed."""


class CodexSubscriptionTimeoutError(CodexSubscriptionTransportError):
    """The configured operation timeout elapsed."""


class CodexSubscriptionResponseError(CodexSubscriptionAdapterError):
    """The final response did not satisfy the requested structured contract."""


class CodexSubscriptionToolUseError(CodexSubscriptionAdapterError):
    """A supposedly tool-free turn attempted to use a tool."""


class CodexSubscriptionClosedError(CodexSubscriptionAdapterError):
    """An operation was attempted after the adapter was closed."""


@dataclass(frozen=True, slots=True)
class CodexRuntimeMetadata:
    sdk_version: str | None = None
    runtime_name: str | None = None
    runtime_version: str | None = None
    user_agent: str | None = None
    platform_family: str | None = None
    platform_os: str | None = None


@dataclass(frozen=True, slots=True)
class CodexTokenUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    reasoning_output_tokens: int | None = None
    cached_input_tokens: int | None = None
    cache_write_input_tokens: int | None = None
    total_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class CodexModelUsage(ModelUsage):
    """Provider usage plus App Server correlation and runtime fields."""

    thread_id: str | None = None
    turn_id: str | None = None
    requested_model_id: str | None = None
    requested_effort: str | None = None
    rerouted_from_model: str | None = None
    sdk_version: str | None = None
    runtime_name: str | None = None
    runtime_version: str | None = None
    tool_events_audited: bool = False
    web_search_events: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True, slots=True)
class _RunRequest:
    model: str
    effort: str
    prompt: str
    payload_text: str
    output_schema: dict[str, Any]
    cwd: str


@dataclass(frozen=True, slots=True)
class _TurnRecord:
    thread_id: str
    turn_id: str
    final_response: str | None
    status: str
    effective_model: str
    effective_effort: str
    usage: CodexTokenUsage | None = None
    rerouted_from_model: str | None = None
    forbidden_item_types: tuple[str, ...] = ()
    events_audited: bool = True
    web_search_events: tuple[dict[str, Any], ...] = ()


class _Session(Protocol):
    metadata: CodexRuntimeMetadata

    def run(self, request: _RunRequest) -> _TurnRecord: ...

    def account_read(self) -> Mapping[str, Any]: ...

    def model_list(self) -> Mapping[str, Any]: ...

    def rate_limits_read(self) -> Mapping[str, Any]: ...

    def account_usage_read(self) -> Mapping[str, Any]: ...

    def close(self) -> None: ...


class _SessionFailure(RuntimeError):
    def __init__(self, kind: str, *, transient: bool, discard: bool = False) -> None:
        super().__init__(kind)
        self.kind = kind
        self.transient = transient
        self.discard = discard


class _ForbiddenToolUse(RuntimeError):
    def __init__(self, item_types: tuple[str, ...]) -> None:
        super().__init__("forbidden tool use")
        self.item_types = item_types


class _SdkCodexSession:
    """Small SDK-specific layer; low-level requests are limited to official probes."""

    def __init__(self, cwd: str, *, web_search: bool = False) -> None:
        try:
            import openai_codex as sdk

            config = sdk.CodexConfig(
                cwd=cwd,
                client_name="clarifytrial",
                client_title="ClarifyTrial structured model",
                client_version="0.1.0",
                config_overrides=(
                    _WEB_REVIEW_CONFIG_OVERRIDES
                    if web_search
                    else _HARDENED_CONFIG_OVERRIDES
                ),
            )
            self._sdk = sdk
            self._codex = sdk.Codex(config)
        except Exception:
            raise CodexSubscriptionUnavailableError(
                f"{CODEX_SDK_REQUIREMENT} and its pinned runtime are required"
            ) from None
        self._closed = False
        self._web_search = web_search
        self._allowed_item_types = (
            _WEB_REVIEW_ALLOWED_ITEM_TYPES if web_search else _ALLOWED_ITEM_TYPES
        )
        init = self._codex.metadata
        server = getattr(init, "serverInfo", None)
        self.metadata = CodexRuntimeMetadata(
            sdk_version=_safe_string(getattr(sdk, "__version__", None)),
            runtime_name=_safe_string(getattr(server, "name", None)),
            runtime_version=_safe_string(getattr(server, "version", None)),
            user_agent=_safe_string(getattr(init, "userAgent", None)),
            platform_family=_safe_string(getattr(init, "platformFamily", None)),
            platform_os=_safe_string(getattr(init, "platformOs", None)),
        )

    def run(self, request: _RunRequest) -> _TurnRecord:
        try:
            thread = self._codex.thread_start(
                approval_mode=self._sdk.ApprovalMode.deny_all,
                base_instructions=(
                    _WEB_REVIEW_BASE_INSTRUCTIONS
                    if self._web_search
                    else _BASE_INSTRUCTIONS
                ),
                developer_instructions=request.prompt,
                cwd=request.cwd,
                ephemeral=True,
                model=request.model,
                sandbox=self._sdk.Sandbox.read_only,
                service_name="clarifytrial_structured_model",
            )
            handle = thread.turn(
                request.payload_text,
                approval_mode=self._sdk.ApprovalMode.deny_all,
                cwd=request.cwd,
                effort=request.effort,
                model=request.model,
                output_schema=cast(Any, request.output_schema),
                sandbox=self._sdk.Sandbox.read_only,
            )
            return self._collect_turn(thread.id, handle, request)
        except _ForbiddenToolUse:
            raise
        except _SessionFailure:
            raise
        except Exception as exc:
            transient = bool(self._sdk.is_retryable_error(exc)) or isinstance(
                exc, self._sdk.TransportClosedError
            )
            raise _SessionFailure(
                "transport" if transient else "request",
                transient=transient,
                discard=isinstance(exc, self._sdk.TransportClosedError),
            ) from None

    def _collect_turn(self, thread_id: str, handle: Any, request: _RunRequest) -> _TurnRecord:
        final_response: str | None = None
        fallback_response: str | None = None
        usage: CodexTokenUsage | None = None
        status: str | None = None
        effective_model = request.model
        rerouted_from: str | None = None
        forbidden: set[str] = set()
        web_search_events: list[dict[str, Any]] = []
        interrupted = False
        stream = handle.stream()
        try:
            for event in stream:
                params = _event_params(getattr(event, "payload", None))
                method = _safe_string(getattr(event, "method", None)) or ""
                if method == "model/rerouted":
                    rerouted_from = _mapping_string(params, "fromModel", "from_model")
                    effective_model = (
                        _mapping_string(params, "toModel", "to_model") or effective_model
                    )
                elif method == "thread/tokenUsage/updated":
                    usage = _token_usage_from_event(params)
                elif method in {"item/started", "item/completed"}:
                    item = params.get("item")
                    item_map = _as_mapping(item)
                    item_type = _mapping_string(item_map, "type")
                    if item_type and item_type not in self._allowed_item_types:
                        forbidden.add(item_type)
                        if not interrupted:
                            interrupted = True
                            try:
                                handle.interrupt()
                            except Exception:
                                pass
                    if method == "item/completed" and item_type == "agentMessage":
                        text = _mapping_string(item_map, "text")
                        phase = _mapping_string(item_map, "phase")
                        if text is not None and phase == "final_answer":
                            final_response = text
                        elif text is not None:
                            fallback_response = text
                    elif method == "item/completed" and item_type == "webSearch":
                        web_search_events.append(_compact_web_search_event(item_map))
                elif method == "turn/completed":
                    turn = _as_mapping(params.get("turn"))
                    status = _mapping_string(turn, "status")
                    if status == "failed":
                        error = _as_mapping(turn.get("error"))
                        raise _SessionFailure(
                            "turn_failed",
                            transient=_is_transient_turn_error(error),
                        )
        finally:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
        if forbidden:
            raise _ForbiddenToolUse(tuple(sorted(forbidden)))
        if status is None:
            raise _SessionFailure("missing_completion", transient=True, discard=True)
        return _TurnRecord(
            thread_id=thread_id,
            turn_id=str(handle.id),
            final_response=final_response or fallback_response,
            status=status,
            effective_model=effective_model,
            effective_effort=request.effort,
            usage=usage,
            rerouted_from_model=rerouted_from,
            web_search_events=tuple(web_search_events),
        )

    def account_read(self) -> Mapping[str, Any]:
        return _model_mapping(self._codex.account(refresh_token=False))

    def model_list(self) -> Mapping[str, Any]:
        return _model_mapping(self._codex.models(include_hidden=False))

    def rate_limits_read(self) -> Mapping[str, Any]:
        return self._probe("account/rateLimits/read", "GetAccountRateLimitsResponse")

    def account_usage_read(self) -> Mapping[str, Any]:
        return self._probe("account/usage/read", "GetAccountTokenUsageResponse")

    def _probe(self, method: str, response_name: str) -> Mapping[str, Any]:
        try:
            from openai_codex.generated import v2_all

            response_model = getattr(v2_all, response_name)
            response = self._codex._client.request(  # pinned SDK probe boundary
                method, None, response_model=response_model
            )
            return _model_mapping(response)
        except Exception as exc:
            transient = bool(self._sdk.is_retryable_error(exc)) or isinstance(
                exc, self._sdk.TransportClosedError
            )
            raise _SessionFailure(
                "probe_transport" if transient else "probe_request",
                transient=transient,
                discard=isinstance(exc, self._sdk.TransportClosedError),
            ) from None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._codex.close()


class CodexSubscriptionStructuredModel:
    """Reuse one pinned App Server while isolating every structured call."""

    def __init__(
        self,
        *,
        model_id: str = DEFAULT_CODEX_MODEL,
        effort: str = DEFAULT_CODEX_EFFORT,
        prompt_loader: PromptLoader | None = None,
        timeout_seconds: float = 180,
        max_retries: int = 2,
        retry_delay_seconds: float = 0.5,
        isolated_cwd: str | Path | None = None,
        web_search: bool = False,
        session_factory: Callable[[str], _Session] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.perf_counter,
        request_id_factory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if max_retries < 0:
            raise ValueError("max_retries must not be negative")
        if retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must not be negative")
        if not model_id.strip():
            raise ValueError("model_id must not be empty")
        if effort not in ALLOWED_CODEX_EFFORTS:
            raise ValueError(
                "effort must be one of " + ", ".join(sorted(ALLOWED_CODEX_EFFORTS))
            )
        self._model_id = model_id
        self._effort = effort
        self._prompt_loader = prompt_loader or repository_prompt_loader()
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_delay_seconds = retry_delay_seconds
        self._web_search = web_search
        self._session_factory = session_factory or (
            lambda cwd: _SdkCodexSession(cwd, web_search=web_search)
        )
        self._sleep = sleep
        self._clock = clock
        self._request_id_factory = request_id_factory
        self._session: _Session | None = None
        self._closed = False
        self._lock = threading.RLock()
        self._tempdir: Any | None = None
        if isolated_cwd is None:
            self._tempdir = tempfile.TemporaryDirectory(
                prefix="clarifytrial-codex-", ignore_cleanup_errors=True
            )
            self._cwd = Path(self._tempdir.name).resolve()
        else:
            self._cwd = Path(isolated_cwd).resolve()
        _validate_isolated_cwd(self._cwd)

    def __enter__(self) -> "CodexSubscriptionStructuredModel":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            self.close()
        except Exception:
            if exc_type is None:
                raise

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            session, self._session = self._session, None
            try:
                if session is not None:
                    session.close()
            finally:
                if self._tempdir is not None:
                    self._tempdir.cleanup()
                    self._tempdir = None

    def complete(self, call: ModelCall[ResponseT]) -> tuple[ResponseT, CodexModelUsage]:
        with self._lock:
            self._ensure_open()
            _assert_empty(self._cwd)
            if _contains_sensitive_key(call.payload):
                raise CodexSubscriptionAdapterError(
                    "model payload contains credential-like fields"
                )
            try:
                prompt = self._prompt_loader(call.prompt_id)
                payload_text = json.dumps(
                    {"role": call.role, "payload": call.payload},
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                schema = _strict_output_schema(
                    call.response_model.model_json_schema()
                )
                json.dumps(schema)
            except Exception:
                raise CodexSubscriptionAdapterError(
                    "structured model request could not be prepared"
                ) from None
            request = _RunRequest(
                model=self._model_id,
                effort=self._effort,
                prompt=prompt,
                payload_text=payload_text,
                output_schema=cast(dict[str, Any], schema),
                cwd=str(self._cwd),
            )
            request_id = self._request_id_factory()
            started = self._clock()
            record, attempts, metadata = self._run_with_retries(request)
            latency_ms = max(0, round((self._clock() - started) * 1000))
            _assert_empty(self._cwd)
            if record.forbidden_item_types or not record.events_audited:
                raise CodexSubscriptionToolUseError(
                    "Codex turn did not pass the tool-event audit"
                )
            if record.status != "completed" or record.final_response is None:
                raise CodexSubscriptionResponseError(
                    "Codex turn did not return completed structured output"
                )
            try:
                raw_output = json.loads(record.final_response)
                typed = call.response_model.model_validate(raw_output)
            except (json.JSONDecodeError, ValidationError):
                raise CodexSubscriptionResponseError(
                    "Codex structured output failed contract validation"
                ) from None
            tokens = record.usage or CodexTokenUsage()
            usage = CodexModelUsage(
                model_id=record.effective_model,
                effort=record.effective_effort,
                input_tokens=tokens.input_tokens,
                output_tokens=tokens.output_tokens,
                cache_creation_input_tokens=tokens.cache_write_input_tokens,
                cache_read_input_tokens=tokens.cached_input_tokens,
                thinking_tokens=tokens.reasoning_output_tokens,
                latency_ms=latency_ms,
                finish_reason=record.status,
                request_id=_safe_identifier(request_id),
                attempts=attempts,
                thread_id=_safe_identifier(record.thread_id),
                turn_id=_safe_identifier(record.turn_id),
                requested_model_id=self._model_id,
                requested_effort=self._effort,
                rerouted_from_model=_safe_identifier(record.rerouted_from_model),
                total_tokens=tokens.total_tokens,
                sdk_version=metadata.sdk_version,
                runtime_name=metadata.runtime_name,
                runtime_version=metadata.runtime_version,
                tool_events_audited=True,
                web_search_events=record.web_search_events,
            )
            return typed, usage

    def runtime_metadata(self) -> CodexRuntimeMetadata:
        with self._lock:
            return self._get_session().metadata

    def account_info(self) -> Mapping[str, Any]:
        """Return account type/plan state while always dropping account email."""
        return self._probe("account_read", sanitize_account=True)

    def available_models(self) -> Mapping[str, Any]:
        return self._probe("model_list")

    def rate_limits(self) -> Mapping[str, Any]:
        return self._probe("rate_limits_read")

    def account_usage(self) -> Mapping[str, Any]:
        return self._probe("account_usage_read")

    def _probe(self, method: str, *, sanitize_account: bool = False) -> Mapping[str, Any]:
        with self._lock:
            self._ensure_open()
            result, _, _ = self._operation_with_retries(
                lambda session: getattr(session, method)()
            )
            mapping = _as_mapping(result)
            return _drop_keys(mapping, {"email"}) if sanitize_account else mapping

    def _run_with_retries(
        self, request: _RunRequest
    ) -> tuple[_TurnRecord, int, CodexRuntimeMetadata]:
        result, attempts, metadata = self._operation_with_retries(
            lambda session: session.run(request)
        )
        return cast(_TurnRecord, result), attempts, metadata

    def _operation_with_retries(
        self, operation: Callable[[_Session], Any]
    ) -> tuple[Any, int, CodexRuntimeMetadata]:
        for attempts in range(1, self._max_retries + 2):
            session = self._get_session()
            try:
                value = self._with_timeout(lambda: operation(session), session)
                return value, attempts, session.metadata
            except _ForbiddenToolUse:
                raise CodexSubscriptionToolUseError(
                    "Codex attempted a forbidden tool; output was discarded"
                ) from None
            except _SessionFailure as failure:
                if failure.discard:
                    self._discard_session(session)
                if failure.transient and attempts <= self._max_retries:
                    self._sleep(self._retry_delay_seconds * (2 ** (attempts - 1)))
                    continue
                if failure.kind == "timeout":
                    raise CodexSubscriptionTimeoutError(
                        f"Codex App Server operation timed out after {attempts} attempts"
                    ) from None
                raise CodexSubscriptionTransportError(
                    f"Codex App Server operation failed after {attempts} attempts"
                ) from None
            except Exception:
                self._discard_session(session)
                raise CodexSubscriptionTransportError(
                    f"Codex App Server operation failed after {attempts} attempts"
                ) from None
        raise AssertionError("retry loop ended without a result")

    def _with_timeout(self, operation: Callable[[], Any], session: _Session) -> Any:
        results: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

        def worker() -> None:
            try:
                results.put((True, operation()))
            except BaseException as exc:
                results.put((False, exc))

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            ok, value = results.get(timeout=self._timeout_seconds)
        except queue.Empty:
            raise _SessionFailure("timeout", transient=True, discard=True) from None
        if ok:
            return value
        raise value

    def _get_session(self) -> _Session:
        self._ensure_open()
        if self._session is None:
            try:
                self._session = self._session_factory(str(self._cwd))
            except CodexSubscriptionAdapterError:
                raise
            except Exception:
                raise CodexSubscriptionUnavailableError(
                    "Codex App Server session could not be started"
                ) from None
        return self._session

    def _discard_session(self, session: _Session) -> None:
        if self._session is session:
            self._session = None
        try:
            session.close()
        except Exception:
            pass

    def _ensure_open(self) -> None:
        if self._closed:
            raise CodexSubscriptionClosedError("Codex adapter is closed")


class CodexSubscriptionModelPool:
    """Fixed pool of isolated adapters for truly concurrent independent calls.

    A single :class:`CodexSubscriptionStructuredModel` is thread-safe but
    intentionally serializes operations on its one App Server connection. This
    pool assigns each concurrent call to a different long-lived adapter/session,
    preserving fresh ephemeral threads and empty-workspace isolation per worker.
    """

    def __init__(
        self,
        *,
        size: int = 3,
        worker_factory: Callable[[], CodexSubscriptionStructuredModel] | None = None,
    ) -> None:
        if size <= 0:
            raise ValueError("size must be positive")
        factory = worker_factory or CodexSubscriptionStructuredModel
        workers: list[CodexSubscriptionStructuredModel] = []
        try:
            for _ in range(size):
                workers.append(factory())
        except Exception:
            for worker in workers:
                try:
                    worker.close()
                except Exception:
                    pass
            raise
        self._workers = tuple(workers)
        self._available: queue.LifoQueue[CodexSubscriptionStructuredModel] = (
            queue.LifoQueue(maxsize=size)
        )
        for worker in workers:
            self._available.put_nowait(worker)
        self._condition = threading.Condition(threading.RLock())
        self._active = 0
        self._closed = False

    def __enter__(self) -> "CodexSubscriptionModelPool":
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        try:
            self.close()
        except Exception:
            if exc_type is None:
                raise

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    @property
    def size(self) -> int:
        return len(self._workers)

    def complete(self, call: ModelCall[ResponseT]) -> tuple[ResponseT, CodexModelUsage]:
        return cast(tuple[ResponseT, CodexModelUsage], self._with_worker(
            lambda worker: worker.complete(call)
        ))

    def runtime_metadata(self) -> CodexRuntimeMetadata:
        return cast(
            CodexRuntimeMetadata,
            self._with_worker(lambda worker: worker.runtime_metadata()),
        )

    def account_info(self) -> Mapping[str, Any]:
        return cast(
            Mapping[str, Any],
            self._with_worker(lambda worker: worker.account_info()),
        )

    def available_models(self) -> Mapping[str, Any]:
        return cast(
            Mapping[str, Any],
            self._with_worker(lambda worker: worker.available_models()),
        )

    def rate_limits(self) -> Mapping[str, Any]:
        return cast(
            Mapping[str, Any],
            self._with_worker(lambda worker: worker.rate_limits()),
        )

    def account_usage(self) -> Mapping[str, Any]:
        return cast(
            Mapping[str, Any],
            self._with_worker(lambda worker: worker.account_usage()),
        )

    def close(self) -> None:
        with self._condition:
            if self._closed:
                return
            self._closed = True
            while self._active:
                self._condition.wait()
        failed = False
        for worker in self._workers:
            try:
                worker.close()
            except Exception:
                failed = True
        if failed:
            raise CodexSubscriptionTransportError(
                "one or more Codex pool workers failed to close"
            )

    def _with_worker(self, operation: Callable[[CodexSubscriptionStructuredModel], Any]) -> Any:
        worker = self._borrow()
        try:
            return operation(worker)
        finally:
            self._release(worker)

    def _borrow(self) -> CodexSubscriptionStructuredModel:
        with self._condition:
            if self._closed:
                raise CodexSubscriptionClosedError("Codex adapter pool is closed")
            self._active += 1
        try:
            return self._available.get()
        except BaseException:
            with self._condition:
                self._active -= 1
                self._condition.notify_all()
            raise

    def _release(self, worker: CodexSubscriptionStructuredModel) -> None:
        self._available.put_nowait(worker)
        with self._condition:
            self._active -= 1
            self._condition.notify_all()


def _validate_isolated_cwd(path: Path) -> None:
    if not path.is_dir():
        raise ValueError("isolated_cwd must be an existing directory")
    for candidate in (path, *path.parents):
        if (candidate / ".git").exists():
            raise ValueError("isolated_cwd must be outside a Git worktree")
    _assert_empty(path)


def _assert_empty(path: Path) -> None:
    if any(path.iterdir()):
        raise CodexSubscriptionToolUseError(
            "isolated Codex workspace is not empty; output was discarded"
        )


def _contains_sensitive_key(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if str(key).casefold().replace("-", "_") in _SENSITIVE_KEYS:
                return True
            if _contains_sensitive_key(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _event_params(payload: Any) -> Mapping[str, Any]:
    if isinstance(payload, Mapping):
        return cast(Mapping[str, Any], payload)
    params = getattr(payload, "params", None)
    if isinstance(params, Mapping):
        return cast(Mapping[str, Any], params)
    return _model_mapping(payload)


def _model_mapping(value: Any) -> Mapping[str, Any]:
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        result = dump(by_alias=True, mode="json")
        if isinstance(result, Mapping):
            return cast(Mapping[str, Any], result)
    return _as_mapping(value)


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else {}


def _mapping_string(values: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = values.get(key)
        if isinstance(value, str):
            return value
    return None


def _compact_web_search_event(item: Mapping[str, Any]) -> dict[str, Any]:
    """Keep an auditable search trail without copying page bodies into traces."""

    action = _as_mapping(item.get("action"))
    compact_action = {
        key: value
        for key in ("type", "query", "queries", "url", "pattern")
        if (value := action.get(key)) is not None
    }
    compact_results: list[dict[str, str]] = []
    raw_results = item.get("results")
    if isinstance(raw_results, list):
        for raw in raw_results[:20]:
            result = _as_mapping(raw)
            compact = {
                key: value[:2_000]
                for key in ("title", "url")
                if isinstance((value := result.get(key)), str) and value
            }
            if compact:
                compact_results.append(compact)
    return {
        "query": (_mapping_string(item, "query") or "")[:1_000],
        "action": compact_action,
        "results": compact_results,
    }


def _safe_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value.strip() else None


def _safe_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = "".join(character for character in value if character.isprintable())[:512]
    return cleaned or None


def _optional_count(values: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = values.get(key)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise _SessionFailure("invalid_usage", transient=False)
        return value
    return None


def _token_usage_from_event(params: Mapping[str, Any]) -> CodexTokenUsage:
    usage = _as_mapping(params.get("tokenUsage") or params.get("token_usage"))
    last = _as_mapping(usage.get("last"))
    return CodexTokenUsage(
        input_tokens=_optional_count(last, "inputTokens", "input_tokens"),
        output_tokens=_optional_count(last, "outputTokens", "output_tokens"),
        reasoning_output_tokens=_optional_count(
            last, "reasoningOutputTokens", "reasoning_output_tokens"
        ),
        cached_input_tokens=_optional_count(
            last, "cachedInputTokens", "cached_input_tokens"
        ),
        cache_write_input_tokens=_optional_count(
            last, "cacheWriteInputTokens", "cache_write_input_tokens"
        ),
        total_tokens=_optional_count(last, "totalTokens", "total_tokens"),
    )


def _is_transient_turn_error(error: Mapping[str, Any]) -> bool:
    text = json.dumps(error, ensure_ascii=True, separators=(",", ":")).casefold()
    return any(
        marker in text
        for marker in (
            "serveroverloaded",
            "httpconnectionfailed",
            "responsestreamconnectionfailed",
            "responsestreamdisconnected",
            "responsetoomanyfailedattempts",
            '"httpstatuscode":429',
            '"httpstatuscode":500',
            '"httpstatuscode":502',
            '"httpstatuscode":503',
            '"httpstatuscode":504',
        )
    )


def _drop_keys(value: Any, keys: set[str]) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _drop_keys(item, keys)
            for key, item in value.items()
            if str(key).casefold() not in keys
        }
    if isinstance(value, list):
        return [_drop_keys(item, keys) for item in value]
    return value
