from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from clarifytrial.contracts import (
    ClinicalStatus,
    CriterionAssessment,
    CriterionKind,
    EvidenceSufficiency,
    NextAction,
    PatientState,
    TrialCriterion,
)
from clarifytrial.llm import ScriptedStructuredModel
from clarifytrial.preparation import TrialProtocolCache, TrialProtocolSource
from clarifytrial.preparation.trial_protocol import (
    PreparedInformationNeed,
    PreparedTrial,
    TrialProtocolStructurerAgent,
    merge_information_requests,
    structure_trial_protocol,
)
from clarifytrial.trace import TraceRecorder
from clarifytrial.workflow import ScreeningTrial
from clarifytrial.workflow.patient_screening_rules import aggregate_screening_trial


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


def test_explicit_protocol_section_overrides_a_wrong_model_kind() -> None:
    eligibility = (
        "Inclusion Criteria:\nAge must be at least 18 years.\n"
        "Exclusion Criteria:\nNo active infection.\n"
    )

    def structure_protocol(payload):
        text = payload["eligibility_text"]
        return {
            "criteria": [
                {
                    "kind": "inclusion",
                    "statement": line,
                    "source_quote": line,
                    "information_needs": [],
                }
                for line in text.splitlines()
                if line.endswith("years.") or line.endswith("infection.")
            ]
        }

    result = structure_trial_protocol(
        TrialProtocolSource(
            trial_id="T-SECTIONS",
            title="Synthetic section test",
            eligibility_text=eligibility,
            source_location="synthetic:T-SECTIONS",
        ),
        TrialProtocolStructurerAgent(
            ScriptedStructuredModel(
                {"trial_protocol_structurer": structure_protocol}
            )
        ),
        trace=TraceRecorder("sections"),
    )

    assert [item.kind.value for item in result.trial.criteria] == [
        "inclusion",
        "exclusion",
    ]


def test_unrepresented_alternative_path_cannot_become_confirmed() -> None:
    eligibility = (
        "Inclusion Criteria:\n"
        "At least one of the following must apply: HbA1c below 7% or fasting "
        "glucose below 126 mg/dL.\n"
    )

    def structure_protocol(payload):
        line = next(
            value
            for value in payload["eligibility_text"].splitlines()
            if value.startswith("At least")
        )
        return {
            "criteria": [
                {
                    "kind": "inclusion",
                    "statement": line,
                    "source_quote": line,
                    "information_needs": [],
                }
            ]
        }

    result = structure_trial_protocol(
        TrialProtocolSource(
            trial_id="T-ALTERNATIVE",
            title="Synthetic alternative-path test",
            eligibility_text=eligibility,
            source_location="synthetic:T-ALTERNATIVE",
        ),
        TrialProtocolStructurerAgent(
            ScriptedStructuredModel(
                {"trial_protocol_structurer": structure_protocol}
            )
        ),
        trace=TraceRecorder("alternative"),
    )
    criterion = result.trial.criteria[0]
    decision = aggregate_screening_trial(
        trial=result.trial,
        assessments={
            criterion.criterion_id: CriterionAssessment(
                criterion_id=criterion.criterion_id,
                criterion_source_location=criterion.source_location,
                clinical_status=ClinicalStatus.SUPPORTS,
                evidence_sufficiency=EvidenceSufficiency.SUFFICIENT,
                evidence_ids=["E1"],
                rationale="가상 자료가 추출된 조건을 충족한다.",
            )
        },
        evidence_requests=[],
        patient_state=PatientState(
            patient_id="P1",
            as_of=datetime(2026, 8, 29, tzinfo=UTC),
            facts=[],
        ),
    )

    assert result.trial.protocol_logic_supported is False
    assert result.trial.protocol_logic_issues
    assert decision.candidate_status.value == "retain"
    assert decision.confirmation_status.value == "not_confirmed"


def test_noncontiguous_model_quote_uses_the_real_condition_line() -> None:
    eligibility = (
        "Inclusion Criteria:\n"
        "* Locally advanced disease with one or more of the following\n"
        "* Extensive liver infiltration\n"
        "* Vascular involvement: encasement (>180-degree angle)\n"
    )

    def structure_protocol(_payload):
        return {
            "criteria": [
                {
                    "kind": "inclusion",
                    "statement": "Vascular encasement must exceed 180 degrees.",
                    "source_quote": (
                        "Locally advanced disease with one or more of the following\n"
                        "* Vascular involvement: encasement (>180-degree angle)"
                    ),
                    "numeric_constraint": {
                        "concept": "vascular encasement angle",
                        "operator": "gt",
                        "threshold": 180,
                        "unit": "degree",
                    },
                    "information_needs": [],
                }
            ]
        }

    result = structure_trial_protocol(
        TrialProtocolSource(
            trial_id="T-COMPOSITE",
            title="Synthetic composite quote test",
            eligibility_text=eligibility,
            source_location="synthetic:T-COMPOSITE",
        ),
        TrialProtocolStructurerAgent(
            ScriptedStructuredModel(
                {"trial_protocol_structurer": structure_protocol}
            )
        ),
        trace=TraceRecorder("composite"),
    )

    criterion = result.trial.criteria[0]
    assert criterion.statement == (
        "Vascular involvement: encasement (>180-degree angle)"
    )
    assert result.trial.protocol_logic_supported is False


def test_unverified_numeric_field_is_removed_without_stopping_the_trial() -> None:
    eligibility = "Inclusion Criteria:\nAge must be at least 18 years.\n"

    def structure_protocol(_payload):
        return {
            "criteria": [
                {
                    "kind": "inclusion",
                    "statement": "Age must be at least 18 years.",
                    "source_quote": "Age must be at least 18 years.",
                    "numeric_constraint": {
                        "concept": "age",
                        "operator": "gte",
                        "threshold": 21,
                        "unit": "years",
                    },
                    "information_needs": [],
                }
            ]
        }

    result = structure_trial_protocol(
        TrialProtocolSource(
            trial_id="T-BAD-NUMERIC",
            title="Synthetic unsupported numeric field test",
            eligibility_text=eligibility,
            source_location="synthetic:T-BAD-NUMERIC",
        ),
        TrialProtocolStructurerAgent(
            ScriptedStructuredModel(
                {"trial_protocol_structurer": structure_protocol}
            )
        ),
        trace=TraceRecorder("bad-numeric"),
    )

    assert result.trial.criteria[0].numeric_constraint is None
    assert result.trial.protocol_logic_supported is False
    assert any("수치 조건" in item for item in result.trial.protocol_logic_issues)


def test_same_fact_key_merges_wording_variants_across_trials() -> None:
    prepared = []
    for trial_id, description in (
        ("T1", "Patient's age in years."),
        ("T2", "환자의 현재 연령(년)"),
    ):
        criterion = TrialCriterion(
            criterion_id=f"{trial_id}:age",
            trial_id=trial_id,
            kind=CriterionKind.INCLUSION,
            statement="Age criterion",
            source_location=f"synthetic:{trial_id}",
        )
        prepared.append(
            PreparedTrial(
                trial=ScreeningTrial(trial_id=trial_id, criteria=[criterion]),
                needs=(
                    PreparedInformationNeed(
                        fact_key="age_years",
                        description=description,
                        acceptable_actions=(NextAction.ASK_PATIENT,),
                        criterion_id=criterion.criterion_id,
                    ),
                ),
            )
        )

    requests, _ = merge_information_requests(prepared)

    assert len(requests) == 1
    assert requests[0].description == "Patient's age in years."
    assert requests[0].related_criterion_ids == ["T1:age", "T2:age"]
