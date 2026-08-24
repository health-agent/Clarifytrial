from __future__ import annotations

import json
from pathlib import Path

from clarifytrial.llm import ScriptedStructuredModel
from clarifytrial.preparation import TrialProtocolCache, TrialProtocolSource
from clarifytrial.preparation.trial_protocol import (
    TrialProtocolStructurerAgent,
    structure_trial_protocol,
)
from clarifytrial.trace import TraceRecorder


def _source(threshold: float = 7.0) -> TrialProtocolSource:
    text = f"HbA1c must be below {threshold:.1f} %."
    return TrialProtocolSource(
        trial_id="T-CACHE",
        title="Synthetic diabetes cache study",
        conditions=["type 2 diabetes"],
        summary="Synthetic cache test",
        eligibility_text=text,
        source_location="synthetic:T-CACHE",
    )


def _model() -> ScriptedStructuredModel:
    def structure_trial(payload):
        text = payload["eligibility_text"]
        threshold = 8.0 if "8.0" in text else 7.0
        return {
            "criteria": [
                {
                    "kind": "inclusion",
                    "statement": text,
                    "source_quote": text,
                    "numeric_constraint": {
                        "concept": "hba1c",
                        "operator": "lt",
                        "threshold": threshold,
                        "unit": "%",
                    },
                    "information_needs": [],
                }
            ]
        }

    return ScriptedStructuredModel(
        {"trial_protocol_structurer": structure_trial}
    )


def _cached_structure(
    *,
    cache: TrialProtocolCache,
    source: TrialProtocolSource,
    model: ScriptedStructuredModel,
    trace: TraceRecorder,
):
    agent = TrialProtocolStructurerAgent(model)
    return cache.get_or_structure(
        source,
        known_needs={},
        trace=trace,
        build=lambda: structure_trial_protocol(
            source,
            agent,
            known_needs={},
            trace=trace,
        ),
    )


def test_unchanged_trial_is_structured_once_and_then_reused(tmp_path: Path) -> None:
    model = _model()
    cache_dir = tmp_path / "cache"

    first_cache = TrialProtocolCache(cache_dir, model_label="model-a / medium")
    first = _cached_structure(
        cache=first_cache,
        source=_source(),
        model=model,
        trace=TraceRecorder("first"),
    )
    second_cache = TrialProtocolCache(cache_dir, model_label="model-a / medium")
    second_trace = TraceRecorder("second")
    second = _cached_structure(
        cache=second_cache,
        source=_source(),
        model=model,
        trace=second_trace,
    )

    assert first == second
    assert model.call_count["trial_protocol_structurer"] == 1
    assert first_cache.stats.newly_structured_trial_count == 1
    assert second_cache.stats.reused_trial_count == 1
    assert any(
        event.event == "trial_protocol_reused"
        for event in second_trace.events
    )


def test_changed_trial_text_or_model_does_not_use_an_old_entry(
    tmp_path: Path,
) -> None:
    model = _model()
    cache_dir = tmp_path / "cache"
    _cached_structure(
        cache=TrialProtocolCache(cache_dir, model_label="model-a / medium"),
        source=_source(7.0),
        model=model,
        trace=TraceRecorder("first"),
    )
    changed_text = _cached_structure(
        cache=TrialProtocolCache(cache_dir, model_label="model-a / medium"),
        source=_source(8.0),
        model=model,
        trace=TraceRecorder("changed-text"),
    )
    _cached_structure(
        cache=TrialProtocolCache(cache_dir, model_label="model-b / medium"),
        source=_source(8.0),
        model=model,
        trace=TraceRecorder("changed-model"),
    )

    assert changed_text.trial.criteria[0].numeric_constraint is not None
    assert changed_text.trial.criteria[0].numeric_constraint.threshold == 8.0
    assert model.call_count["trial_protocol_structurer"] == 3


def test_changed_cache_contents_are_ignored_and_rebuilt(tmp_path: Path) -> None:
    model = _model()
    cache_dir = tmp_path / "cache"
    _cached_structure(
        cache=TrialProtocolCache(cache_dir, model_label="model-a / medium"),
        source=_source(),
        model=model,
        trace=TraceRecorder("first"),
    )
    cache_path = next(cache_dir.glob("*.json"))
    payload = json.loads(cache_path.read_text(encoding="utf-8"))
    payload["trial"]["criteria"][0]["numeric_constraint"]["threshold"] = 99.0
    cache_path.write_text(json.dumps(payload), encoding="utf-8")

    second_cache = TrialProtocolCache(cache_dir, model_label="model-a / medium")
    rebuilt = _cached_structure(
        cache=second_cache,
        source=_source(),
        model=model,
        trace=TraceRecorder("rebuilt"),
    )

    assert rebuilt.trial.criteria[0].numeric_constraint is not None
    assert rebuilt.trial.criteria[0].numeric_constraint.threshold == 7.0
    assert model.call_count["trial_protocol_structurer"] == 2
    assert second_cache.stats.invalid_cache_file_count == 1


def test_long_protocol_is_split_at_lines_and_uses_global_source_offsets() -> None:
    eligibility = (
        "HbA1c must be below 7.0 %.\n"
        "Age must be at least 18 years.\n"
    )

    def structure_chunk(payload):
        text = payload["eligibility_text"]
        if "HbA1c" in text:
            concept, operator, threshold, unit = "hba1c", "lt", 7.0, "%"
        else:
            concept, operator, threshold, unit = "age", "gte", 18.0, "years"
        return {
            "criteria": [
                {
                    "kind": "inclusion",
                    "statement": text.strip(),
                    "source_quote": text.strip(),
                    "numeric_constraint": {
                        "concept": concept,
                        "operator": operator,
                        "threshold": threshold,
                        "unit": unit,
                    },
                    "information_needs": [],
                }
            ]
        }

    model = ScriptedStructuredModel(
        {"trial_protocol_structurer": structure_chunk}
    )
    result = structure_trial_protocol(
        TrialProtocolSource(
            trial_id="T-LONG",
            title="Long synthetic protocol",
            conditions=["type 2 diabetes"],
            eligibility_text=eligibility,
            source_location="synthetic:T-LONG",
        ),
        TrialProtocolStructurerAgent(model),
        chunk_char_limit=34,
        trace=TraceRecorder("long"),
    )

    assert model.call_count["trial_protocol_structurer"] == 2
    assert len(result.trial.criteria) == 2
    second_start = eligibility.index("Age must")
    assert f"chars={second_start}-" in result.trial.criteria[1].source_location
