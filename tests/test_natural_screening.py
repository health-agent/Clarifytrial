from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pytest

from clarifytrial.agents import (
    CoordinatorAgent,
    MatcherJudgeAgent,
    NextEvidenceAgent,
    SelectiveReviewerAgent,
)
from clarifytrial.contracts import (
    EvidenceFact,
    EvidenceSourceType,
    VerificationStatus,
)
from clarifytrial.llm import ModelUsage, ScriptedStructuredModel
from clarifytrial.preparation import (
    InMemoryCandidateSearch,
    NaturalHiddenFactAnswer,
    NaturalScreeningPipeline,
    NaturalScreeningRequest,
    RawPatientRecord,
    TrialGPTCandidateSearch,
    TrialProtocolSource,
    build_synthetic_information_tools,
    summarize_model_usage,
)
from clarifytrial.preparation.patient_record import (
    PatientRecordStructurerAgent,
    structure_patient_record,
)
from clarifytrial.preparation.trial_protocol import TrialProtocolStructurerAgent
from clarifytrial.retrieval import TrialGPTRetrievalConfig, TrialGPTRuntimeSearch
from clarifytrial.settings import EpisodeSettings
from clarifytrial.trace import TraceRecorder
from clarifytrial.workflow import (
    EpisodeAgents,
    PatientScreeningRunner,
    PatientScreeningStopReason,
)


PATIENT_TEXT = (
    "The patient has type 2 diabetes. "
    "HbA1c was 6.5 % on 2026-05-01."
)
TRIAL_A_TEXT = (
    "An official lab result must show HbA1c below 7.0 % and must be measured "
    "within 14 days."
)
TRIAL_B_TEXT = (
    "An official lab result must show HbA1c below 8.0 % and must be measured "
    "within 14 days."
)


def _sources() -> list[TrialProtocolSource]:
    return [
        TrialProtocolSource(
            trial_id="NCT-SYNTH-A",
            title="Type 2 diabetes glucose study",
            conditions=["type 2 diabetes"],
            summary="A synthetic diabetes candidate.",
            eligibility_text=TRIAL_A_TEXT,
            source_location="synthetic-protocol:A",
        ),
        TrialProtocolSource(
            trial_id="NCT-SYNTH-B",
            title="Type 2 diabetes monitoring study",
            conditions=["type 2 diabetes"],
            summary="Another synthetic diabetes candidate.",
            eligibility_text=TRIAL_B_TEXT,
            source_location="synthetic-protocol:B",
        ),
        TrialProtocolSource(
            trial_id="NCT-SYNTH-C",
            title="Major depression study",
            conditions=["major depressive disorder"],
            summary="An unrelated synthetic candidate.",
            eligibility_text="Current major depressive disorder is required.",
            source_location="synthetic-protocol:C",
        ),
    ]


def _model(
    *,
    formatting_variant: bool = False,
    wrong_search_condition: bool = False,
    missing_patient_quote: bool = False,
    wrong_patient_value: bool = False,
    wrong_patient_date: bool = False,
    wrong_trial_threshold: bool = False,
    wrong_trial_operator: bool = False,
    wrong_evidence_age: bool = False,
) -> ScriptedStructuredModel:
    def structure_patient(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        text = payload["record_text"]
        condition_quote = (
            "TYPE 2\nDIABETES" if formatting_variant else "type 2 diabetes"
        )
        condition_start = text.index("type 2 diabetes")
        if formatting_variant:
            condition_start -= 1
        quote = (
            "LDL was 6.5 % on 2026-05-01."
            if missing_patient_quote
            else (
                "HbA1c was 6.5% on\n2026-05-01."
                if formatting_variant
                else "HbA1c was 6.5 % on 2026-05-01."
            )
        )
        start = text.index("HbA1c was 6.5 % on 2026-05-01.")
        if formatting_variant:
            start -= 1
        return {
            "search_conditions": [
                {
                    "condition": (
                        "major depressive disorder"
                        if wrong_search_condition
                        else "type 2 diabetes"
                    ),
                    "source_quote": condition_quote,
                    "start_char": condition_start,
                    "end_char": condition_start + len("type 2 diabetes"),
                }
            ],
            "facts": [
                {
                    "fact_key": "historical_hba1c",
                    "statement": "과거 HbA1c는 6.5%였다.",
                    "source_quote": quote,
                    "start_char": start,
                    "end_char": start + len("HbA1c was 6.5 % on 2026-05-01."),
                    "event_date": (
                        "2026-05-02" if wrong_patient_date else "2026-05-01"
                    ),
                    "concept": "hba1c",
                    "value": 6.4 if wrong_patient_value else 6.5,
                    "unit": "percent" if formatting_variant else "%",
                }
            ],
        }

    def structure_trial(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        text = payload["eligibility_text"]
        threshold = 7.0 if payload["trial_id"].endswith("A") else 8.0
        structured_threshold = 9.0 if wrong_trial_threshold else threshold
        return {
            "criteria": [
                {
                    "kind": "inclusion",
                    "statement": f"HbA1c가 {threshold:g}% 미만이어야 한다.",
                    "source_quote": (
                        text.replace(" ", "\n") if formatting_variant else text
                    ),
                    "start_char": None,
                    "end_char": None,
                    "numeric_constraint": {
                        "concept": "hba1c",
                        "operator": "gte" if wrong_trial_operator else "lt",
                        "threshold": structured_threshold,
                        "unit": "percent" if formatting_variant else "%",
                    },
                    "evidence_requirement": {
                        "max_age_days": 30 if wrong_evidence_age else 14,
                        "allowed_source_types": ["official_verification"],
                        "allowed_verification_statuses": ["verified"],
                    },
                    "information_needs": [
                        {
                            "fact_key": "recent_hba1c",
                            "description": f"모델이 시험 {threshold:g}에 맞춰 달리 쓴 설명",
                            "acceptable_actions": ["ASK_PATIENT"],
                        }
                    ],
                }
            ]
        }

    def coordinate(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "route": payload["allowed_routes"][0],
            "target_ids": payload["required_target_ids"],
            "reason_code": "code_allowed_transition",
            "reason": "코드가 정한 단계와 대상을 그대로 따른다.",
        }

    def match(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        assessments = []
        for criterion in payload["criteria"]:
            criterion_id = criterion["criterion_id"]
            checked = payload["mechanical_checks"][criterion_id]
            missing = []
            if checked["evidence_sufficiency"] != "sufficient":
                missing = [
                    item["fact_id"]
                    for item in payload["evidence_requests"]
                    if criterion_id in item["related_criterion_ids"]
                ]
            assessments.append(
                {
                    "criterion_id": criterion_id,
                    "criterion_source_location": criterion["source_location"],
                    "clinical_status": checked["clinical_status"],
                    "evidence_sufficiency": checked["evidence_sufficiency"],
                    "evidence_ids": checked["evidence_ids"],
                    "missing_information_ids": missing,
                    "rationale": "구조화된 수치와 기간을 코드 결과에 맞춰 판단했다.",
                    "review_flags": [],
                }
            )
        return {"assessments": assessments}

    def write_request(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            **payload["required_action"],
            "reason": "두 후보의 현재 확인에 같은 최근 결과가 필요하다.",
            "message": "최근 14일 안에 받은 공식 HbA1c 결과를 확인해 주세요.",
        }

    return ScriptedStructuredModel(
        {
            "patient_record_structurer": structure_patient,
            "trial_protocol_structurer": structure_trial,
            "coordinator": coordinate,
            "matcher_judge": match,
            "next_evidence": write_request,
            "selective_reviewer": lambda _: pytest.fail(
                "the cited synthetic flow should not require review"
            ),
        }
    )


def _pipeline(model: ScriptedStructuredModel) -> NaturalScreeningPipeline:
    episode_agents = EpisodeAgents(
        coordinator=CoordinatorAgent(model),
        matcher_judge=MatcherJudgeAgent(model),
        next_evidence=NextEvidenceAgent(model),
        selective_reviewer=SelectiveReviewerAgent(model),
    )
    return NaturalScreeningPipeline(
        patient_structurer=PatientRecordStructurerAgent(model),
        trial_structurer=TrialProtocolStructurerAgent(model),
        candidate_search=InMemoryCandidateSearch(_sources()),
        screening_runner=PatientScreeningRunner(
            episode_agents,
            EpisodeSettings(
                max_external_actions=1,
                max_selective_reviews=0,
                max_cycles=6,
            ),
        ),
    )


def _request() -> NaturalScreeningRequest:
    return NaturalScreeningRequest(
        case_id="natural-synthetic-diabetes",
        patient_record=RawPatientRecord(
            patient_id="SYNTHETIC-NATURAL-001",
            source_id="synthetic-note-001",
            text=PATIENT_TEXT,
            recorded_at=datetime(2026, 8, 21, tzinfo=timezone.utc),
            as_of=datetime(2026, 8, 21, tzinfo=timezone.utc),
            source_type=EvidenceSourceType.MEDICAL_RECORD,
            verification_status=VerificationStatus.REPORTED,
        ),
        candidate_count=2,
        acquisition_paths=[
            {
                "fact_key": "recent_hba1c",
                "fact_description": "최근 14일 안의 공식 HbA1c 결과",
                "path_key": "existing-official-result",
                "action": "REQUEST_VERIFICATION",
                "acquisition_mode": "existing_official_result",
                "available_now": True,
                "expected_delay_hours": 2,
                "visit_required": False,
                "direct_cost_band": "none",
                "physical_burden_0_to_3": 0,
                "emotional_burden_0_to_3": 0,
                "medical_risk_0_to_3": 0,
                "treatment_disruption_0_to_3": 0,
                "source_note": "합성 환경에서 이미 받은 공식 결과를 확인",
            }
        ],
    )


def _tool_factory(prepared):
    evidence = EvidenceFact(
        evidence_id="synthetic-recent-hba1c",
        statement="최근 공식 HbA1c는 6.4%였다.",
        source_type=EvidenceSourceType.OFFICIAL_VERIFICATION,
        source_location="synthetic-lab#hba1c",
        event_date=date(2026, 8, 20),
        recorded_date=date(2026, 8, 21),
        verification_status=VerificationStatus.VERIFIED,
        concept="HbA1c",
        value=6.4,
        unit="%",
    )
    return build_synthetic_information_tools(
        prepared,
        [
            NaturalHiddenFactAnswer(
                fact_key="recent_hba1c",
                access_path="REQUEST_VERIFICATION",
                evidence=evidence,
            )
        ],
    )


def test_natural_sources_run_through_search_structure_and_rejudgment() -> None:
    model = _model()
    trace = TraceRecorder("natural-synthetic-diabetes")
    result = _pipeline(model).run(_request(), _tool_factory, trace=trace)

    assert [item.source.trial_id for item in result.prepared.candidate_hits] == [
        "NCT-SYNTH-A",
        "NCT-SYNTH-B",
    ]
    assert len(result.prepared.screening_case.evidence_requests) == 1
    request = result.prepared.screening_case.evidence_requests[0]
    assert request.description == "최근 14일 안의 공식 HbA1c 결과"
    assert [item.value for item in request.acceptable_actions] == [
        "REQUEST_VERIFICATION"
    ]
    assert result.screening.stop_reason is PatientScreeningStopReason.ALL_TRIALS_RESOLVED
    assert len(result.screening.action_history) == 1
    assert all(
        item.confirmation_status.value == "confirmed"
        for item in result.screening.final_decisions
    )
    views = result.screening.guidance.recommendation_views
    assert [item.trial_id for item in views.current_evidence.trials] == [
        "NCT-SYNTH-A",
        "NCT-SYNTH-B",
    ]
    assert [item.trial_id for item in views.broader_review.trials] == [
        "NCT-SYNTH-A",
        "NCT-SYNTH-B",
    ]
    actors = {item.actor for item in trace.events}
    assert {
        "patient_record_source_checks",
        "candidate_trial_search",
        "trial_protocol_source_checks",
        "information_planning_rules",
    }.issubset(actors)
    assert model.call_count == {
        "patient_record_structurer": 1,
        "trial_protocol_structurer": 2,
        "matcher_judge": 2,
        "next_evidence": 1,
    }
    assert result.usage.call_count == 6
    assert result.usage.total_tokens == 0
    assert result.usage.by_role["trial_protocol_structurer"].call_count == 2
    structurer_event = next(
        item for item in trace.events if item.actor == "patient_record_structurer"
    )
    response_trace = structurer_event.output["response"]
    assert response_trace == {
        "search_condition_count": 1,
        "fact_count": 1,
        "fact_keys": ["historical_hba1c"],
        "structured_value_fact_count": 1,
    }
    assert PATIENT_TEXT not in json.dumps(
        structurer_event.model_dump(mode="json"), ensure_ascii=False
    )


def test_formatting_differences_and_wrong_offset_hints_are_accepted() -> None:
    prepared = _pipeline(_model(formatting_variant=True)).prepare(_request())

    evidence = prepared.patient_state.facts[0]
    assert evidence.value == 6.5
    assert evidence.unit == "percent"
    assert evidence.statement == "HbA1c was 6.5 % on 2026-05-01."
    source_quote = "HbA1c was 6.5 % on 2026-05-01."
    expected_start = PATIENT_TEXT.index(source_quote)
    assert evidence.source_location.endswith(
        f"#chars={expected_start}-{expected_start + len(source_quote)}"
    )
    assert prepared.screening_case.trials[0].criteria[0].statement in {
        TRIAL_A_TEXT,
        TRIAL_B_TEXT,
    }


def test_candidate_search_can_use_an_inferred_condition_with_cited_support() -> None:
    trace = TraceRecorder("inferred-search-condition")
    _, search_conditions = structure_patient_record(
        _request().patient_record,
        PatientRecordStructurerAgent(_model(wrong_search_condition=True)),
        trace=trace,
    )

    assert search_conditions == ["major depressive disorder"]
    source_event = next(
        item for item in trace.events if item.actor == "patient_record_source_checks"
    )
    assert source_event.output["source_matches"][0]["query_basis"] == (
        "model_inference_from_cited_record"
    )


def test_source_quote_that_does_not_exist_is_rejected() -> None:
    with pytest.raises(ValueError, match="quoted source text was not found"):
        _pipeline(_model(missing_patient_quote=True)).prepare(_request())


@pytest.mark.parametrize(
    ("model", "message"),
    [
        (_model(wrong_patient_value=True), "patient value 6.4"),
        (_model(wrong_patient_date=True), "event date"),
    ],
)
def test_patient_values_that_change_a_decision_need_source_support(
    model: ScriptedStructuredModel,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        _pipeline(model).prepare(_request())


@pytest.mark.parametrize(
    ("model", "removed_field"),
    [
        (_model(wrong_trial_threshold=True), "numeric_constraint"),
        (_model(wrong_trial_operator=True), "numeric_constraint"),
        (_model(wrong_evidence_age=True), "evidence_requirement"),
    ],
)
def test_unverified_trial_fields_are_not_used_for_automatic_confirmation(
    model: ScriptedStructuredModel,
    removed_field: str,
) -> None:
    prepared = _pipeline(model).prepare(_request())

    for trial in prepared.screening_case.trials:
        criterion = trial.criteria[0]
        assert getattr(criterion, removed_field) is None
        assert trial.protocol_logic_supported is False
        assert trial.protocol_logic_issues


def test_trialgpt_runtime_search_can_serve_one_patient_query(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus.jsonl"
    rows = [
        {
            "_id": "NCT-DIABETES",
            "title": "Type 2 diabetes trial",
            "text": "Adults with type 2 diabetes and elevated HbA1c.",
            "metadata": {"diseases_list": ["type 2 diabetes"]},
        },
        {
            "_id": "NCT-DEPRESSION",
            "title": "Depression trial",
            "text": "Adults with major depressive disorder.",
            "metadata": {"diseases_list": ["major depressive disorder"]},
        },
        {
            "_id": "NCT-CANCER",
            "title": "Breast cancer trial",
            "text": "Adults with breast cancer.",
            "metadata": {"diseases_list": ["breast cancer"]},
        },
    ]
    corpus.write_text(
        "".join(json.dumps(item) + "\n" for item in rows),
        encoding="utf-8",
    )
    runtime = TrialGPTRuntimeSearch(
        corpus,
        tmp_path / "cache",
        TrialGPTRetrievalConfig(
            corpus_name="trec_2021",
            search_depth=3,
            bm25_weight=1,
            medcpt_weight=0,
            device="cpu",
        ),
    )
    hits = TrialGPTCandidateSearch(runtime).search(
        ["type 2 diabetes"],
        top_k=1,
    )

    assert len(hits) == 1
    assert hits[0].source.trial_id == "NCT-DIABETES"
    assert hits[0].retrieval_method == "TrialGPT BM25"


def test_natural_screening_example_files_match_public_contracts() -> None:
    source = Path("examples/natural_screening")
    request = NaturalScreeningRequest.model_validate_json(
        (source / "request.json").read_text(encoding="utf-8")
    )
    trial_rows = json.loads(
        (source / "trial_sources.json").read_text(encoding="utf-8")
    )
    answer_rows = json.loads(
        (source / "hidden_answers.json").read_text(encoding="utf-8")
    )

    trials = [TrialProtocolSource.model_validate(item) for item in trial_rows]
    answers = [NaturalHiddenFactAnswer.model_validate(item) for item in answer_rows]

    assert request.case_id == "synthetic-natural-diabetes"
    assert InMemoryCandidateSearch(trials).search(
        ["type 2 diabetes"], top_k=2
    )[0].source.trial_id == "NCT-SYNTH-A"
    assert answers[0].fact_key == request.acquisition_paths[0].fact_key


def test_usage_summary_includes_preparation_and_screening_roles() -> None:
    trace = TraceRecorder("usage-test")
    trace.record(
        cycle=0,
        actor="patient_record_structurer",
        event="structured_model_completed",
        usage=ModelUsage(
            model_id="test",
            input_tokens=10,
            output_tokens=2,
            thinking_tokens=1,
            total_tokens=12,
        ),
    )
    trace.record(
        cycle=1,
        actor="matcher_judge",
        event="structured_model_completed",
        usage=ModelUsage(
            model_id="test",
            input_tokens=5,
            output_tokens=3,
        ),
    )

    summary = summarize_model_usage(trace)

    assert summary.call_count == 2
    assert summary.input_tokens == 15
    assert summary.output_tokens == 5
    assert summary.thinking_tokens == 1
    assert summary.total_tokens == 20
    assert summary.calls_with_provider_total == 1
    assert summary.by_role["matcher_judge"].total_tokens == 8
