from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from clarifytrial.llm import ModelCall, ScriptedStructuredModel
from clarifytrial.trace import TraceRecorder


class ExampleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str


def test_scripted_model_validates_structured_output() -> None:
    model = ScriptedStructuredModel({"matcher": lambda payload: {"value": payload["value"]}})
    response, usage = model.complete(
        ModelCall(
            role="matcher",
            prompt_id="matcher-v1",
            payload={"value": "ok"},
            response_model=ExampleResponse,
        )
    )

    assert response.value == "ok"
    assert usage.model_id == "scripted-local"
    assert model.call_count["matcher"] == 1


def test_trace_is_jsonl_and_contains_no_hidden_payload(tmp_path) -> None:
    trace = TraceRecorder("case-1")
    trace.record(
        cycle=0,
        actor="coordinator",
        event="route_selected",
        input_refs=["criterion-1"],
        output={"route": "matcher"},
    )
    output = trace.write_jsonl(tmp_path / "trace.jsonl")

    row = json.loads(output.read_text(encoding="utf-8"))
    assert row["sequence"] == 1
    assert row["input_refs"] == ["criterion-1"]
    assert "hidden" not in row
