from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from clarifytrial.app.challenge import (
    ChallengeRunOptions,
    ChallengeTopic,
    add_direct_input_options,
    challenge_topic_request,
    load_challenge_topic_settings,
    load_challenge_topics,
    materialize_prepared_topic,
    run_challenge_screening,
)
from clarifytrial.app.loaders import load_general_patient, load_structured_trials
from clarifytrial.cli import _parser, main
from clarifytrial.contracts import (
    AgentAction,
    CandidateStatus,
    ClinicalStatus,
    ConfirmationStatus,
    CriterionAssessment,
    CriterionKind,
    EvidenceSufficiency,
    NextAction,
    NextEvidenceRequest,
    PatientState,
    TrialCriterion,
    TrialDecision,
    TrialSearchRank,
)
from clarifytrial.llm import ScriptedStructuredModel
from clarifytrial.preparation import (
    InMemoryCandidateSearch,
    PreparedScreeningCase,
    TrialProtocolSource,
)
from clarifytrial.preparation.contracts import CandidateSearchHit
from clarifytrial.reporting import build_recommendation_views
from clarifytrial.settings import EpisodeSettings
from clarifytrial.workflow import PatientScreeningCase, ScreeningTrial


def _request(fact_id: str) -> NextEvidenceRequest:
    return NextEvidenceRequest(
        fact_id=fact_id,
        description=f"{fact_id} 확인",
        related_criterion_ids=["T1:include:001"],
        acceptable_actions=[NextAction.ASK_PATIENT, NextAction.LOOKUP_RECORD],
        reason="이 조건을 판단하는 데 필요하다.",
    )


def _decision(
    trial_id: str,
    *,
    confirmed: bool,
    missing_count: int = 0,
) -> TrialDecision:
    return TrialDecision(
        trial_id=trial_id,
        candidate_status=CandidateStatus.RETAIN,
        confirmation_status=(
            ConfirmationStatus.CONFIRMED
            if confirmed
            else ConfirmationStatus.NOT_CONFIRMED
        ),
        criterion_assessments=[
            CriterionAssessment(
                criterion_id=f"{trial_id}:include:001",
                criterion_source_location=f"trial:{trial_id}#criterion=1",
                clinical_status=(
                    ClinicalStatus.SUPPORTS
                    if confirmed
                    else ClinicalStatus.UNKNOWN
                ),
                evidence_sufficiency=(
                    EvidenceSufficiency.SUFFICIENT
                    if confirmed
                    else EvidenceSufficiency.INSUFFICIENT
                ),
                rationale="합성 시험용 판정",
            )
        ],
        pending_information=[
            NextEvidenceRequest(
                fact_id=f"{trial_id}-missing-{index}",
                description="추가 정보",
                related_criterion_ids=[f"{trial_id}:include:001"],
                acceptable_actions=[NextAction.ASK_PATIENT],
                reason="현재 기록에 없다.",
            )
            for index in range(missing_count)
        ],
        next_action=AgentAction(
            action=NextAction.NONE,
            reason="이 테스트에서는 다음 질문을 실행하지 않는다.",
        ),
    )


def _prepared_case() -> PreparedScreeningCase:
    state = PatientState(
        patient_id="S001",
        as_of=datetime(2026, 8, 24, tzinfo=timezone.utc),
        facts=[],
    )
    criterion = TrialCriterion(
        criterion_id="T1:include:001",
        trial_id="T1",
        kind=CriterionKind.INCLUSION,
        statement="Synthetic diagnosis is required.",
        source_location="trial:T1#criterion=1",
    )
    source = TrialProtocolSource(
        trial_id="T1",
        title="Synthetic trial",
        conditions=["synthetic diagnosis"],
        summary="Synthetic summary",
        eligibility_text="Synthetic diagnosis is required.",
        source_location="trial:T1",
    )
    hit = CandidateSearchHit(
        rank=1,
        score=0.75,
        retrieval_method="test-search",
        source=source,
    )
    request = _request("diagnosis-status")
    screening = PatientScreeningCase(
        case_id="S001",
        disease_group="synthetic diagnosis",
        trials=[ScreeningTrial(trial_id="T1", criteria=[criterion])],
        initial_patient_state=state,
        evidence_requests=[request],
        candidate_ranking=[
            TrialSearchRank(
                trial_id="T1",
                rank=1,
                score=0.75,
                retrieval_method="test-search",
            )
        ],
    )
    return PreparedScreeningCase(
        request_case_id="S001",
        patient_state=state,
        search_conditions=["synthetic diagnosis"],
        candidate_hits=[hit],
        fact_id_by_key={"diagnosis_status": "diagnosis-status"},
        screening_case=screening,
    )


def test_competition_topics_json_is_read_without_changing_its_shape(
    tmp_path: Path,
) -> None:
    source = tmp_path / "topics.json"
    source.write_text(
        json.dumps(
            {
                "topics": [
                    {"num": "S001", "title": "A synthetic patient has diabetes."},
                    {"num": "S002", "title": "A synthetic patient has asthma."},
                ]
            }
        ),
        encoding="utf-8",
    )

    document = load_challenge_topics(source)
    request = challenge_topic_request(
        document.topics[0],
        source_path=source,
        as_of=datetime(2026, 8, 24, tzinfo=timezone.utc),
        candidate_count=10,
    )

    assert [item.num for item in document.topics] == ["S001", "S002"]
    assert request.patient_record.text == "A synthetic patient has diabetes."
    assert request.patient_record.source_type.value == "synthetic_case"
    assert request.candidate_count == 10


def test_optional_topic_settings_add_patient_limits_without_changing_topics(
    tmp_path: Path,
) -> None:
    settings_path = tmp_path / "topic-settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "topic_settings": [
                    {
                        "num": "S001",
                        "patient_burden_input": {
                            "travel_constraint_0_to_3": 3,
                            "preference_mode": "least_extra_burden",
                            "stated_limits": {
                                "max_additional_visits": 0,
                                "allow_new_tests": False,
                            },
                        },
                        "acquisition_paths": [
                            {
                                "fact_key": "recent_hba1c",
                                "fact_description": "최근 HbA1c 결과",
                                "path_key": "outside-record",
                                "action": "LOOKUP_RECORD",
                                "acquisition_mode": "outside_record",
                                "available_now": True,
                                "expected_delay_hours": 2,
                                "visit_required": False,
                                "direct_cost_band": "none",
                                "new_test_required": False,
                                "source_note": "기존 외부 기록을 가져오는 경로",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    settings = load_challenge_topic_settings(settings_path).topic_settings[0]
    request = challenge_topic_request(
        ChallengeTopic(num="S001", title="A synthetic patient has diabetes."),
        source_path=tmp_path / "topics.json",
        as_of=datetime(2026, 8, 24, tzinfo=timezone.utc),
        candidate_count=10,
        topic_settings=settings,
    )

    assert request.patient_burden_input is not None
    assert request.patient_burden_input.travel_constraint_0_to_3 == 3
    assert request.patient_burden_input.stated_limits is not None
    assert request.patient_burden_input.stated_limits.allow_new_tests is False
    assert request.acquisition_paths[0].fact_key == "recent_hba1c"


def test_direct_input_paths_never_invent_a_new_test() -> None:
    prepared = add_direct_input_options(_prepared_case())

    options = prepared.screening_case.acquisition_options
    assert {item.action for item in options} == {
        NextAction.ASK_PATIENT,
        NextAction.LOOKUP_RECORD,
    }
    assert all(item.available_now for item in options)
    assert all(item.new_test_required is False for item in options)


def test_prepared_topic_is_saved_in_the_existing_structured_contracts(
    tmp_path: Path,
) -> None:
    prepared = add_direct_input_options(_prepared_case())
    patient_path, trials_path = materialize_prepared_topic(
        topic=ChallengeTopic(num="S001", title="Synthetic patient text."),
        prepared=prepared,
        output_dir=tmp_path,
    )

    patient = load_general_patient(patient_path)
    trials = load_structured_trials(trials_path)

    assert patient.case_id == "S001"
    assert patient.search_conditions == ["synthetic diagnosis"]
    assert [item.trial_id for item in trials] == ["T1"]
    assert (tmp_path / "prepared-input.json").is_file()


def test_recommendations_show_the_rule_used_for_their_order() -> None:
    decisions = [
        _decision("T-PENDING-TWO", confirmed=False, missing_count=2),
        _decision("T-CONFIRMED", confirmed=True),
        _decision("T-PENDING-ONE", confirmed=False, missing_count=1),
    ]
    ranking = [
        TrialSearchRank(
            trial_id="T-PENDING-TWO",
            rank=1,
            score=0.9,
            retrieval_method="test-search",
        ),
        TrialSearchRank(
            trial_id="T-PENDING-ONE",
            rank=2,
            score=0.8,
            retrieval_method="test-search",
        ),
        TrialSearchRank(
            trial_id="T-CONFIRMED",
            rank=3,
            score=0.7,
            retrieval_method="test-search",
        ),
    ]

    views = build_recommendation_views(decisions, ranking)

    assert [item.trial_id for item in views.broader_review.trials] == [
        "T-CONFIRMED",
        "T-PENDING-ONE",
        "T-PENDING-TWO",
    ]
    assert [item.recommendation_rank for item in views.broader_review.trials] == [
        1,
        2,
        3,
    ]
    assert views.broader_review.trials[1].search_rank == 2
    assert "확인할 정보가 1개" in (
        views.broader_review.trials[1].ranking_explanation or ""
    )


def test_challenge_command_requires_live_model_confirmation(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as error:
        main(
            [
                "run-challenge",
                "--topics",
                str(tmp_path / "topics.json"),
                "--topic-id",
                "S001",
                "--output",
                str(tmp_path / "output"),
            ]
        )
    assert error.value.code == 2


def test_challenge_default_search_needs_no_trialgpt_cache(tmp_path: Path) -> None:
    args = _parser().parse_args(
        [
            "run-challenge",
            "--topics",
            str(tmp_path / "topics.json"),
            "--topic-id",
            "S001",
            "--output",
            str(tmp_path / "output"),
            "--confirm-model-run",
        ]
    )

    assert args.candidate_search == "clinicaltrials"
    assert args.trialgpt_corpus is None
    assert args.trialgpt_cache is None


def test_one_topic_runs_from_free_text_to_ranked_result(tmp_path: Path) -> None:
    patient_text = "The synthetic patient has type 2 diabetes. HbA1c is 6.5 %."
    criterion_text = "HbA1c must be below 7.0 %."
    topics_path = tmp_path / "topics.json"
    topics_path.write_text(
        json.dumps({"topics": [{"num": "S001", "title": patient_text}]}),
        encoding="utf-8",
    )
    search = InMemoryCandidateSearch(
        [
            TrialProtocolSource(
                trial_id="T1",
                title="Type 2 diabetes study",
                conditions=["type 2 diabetes"],
                summary="Synthetic study",
                eligibility_text=criterion_text,
                source_location="synthetic:T1",
            )
        ]
    )

    def structure_patient(_):
        return {
            "search_conditions": [
                {
                    "condition": "type 2 diabetes",
                    "source_quote": "type 2 diabetes",
                }
            ],
            "facts": [
                {
                    "fact_key": "hba1c",
                    "statement": "HbA1c is 6.5 %.",
                    "source_quote": "HbA1c is 6.5 %.",
                    "concept": "hba1c",
                    "value": 6.5,
                    "unit": "%",
                }
            ],
        }

    def structure_trial(_):
        return {
            "criteria": [
                {
                    "kind": "inclusion",
                    "statement": criterion_text,
                    "source_quote": criterion_text,
                    "numeric_constraint": {
                        "concept": "hba1c",
                        "operator": "lt",
                        "threshold": 7.0,
                        "unit": "%",
                    },
                    "information_needs": [],
                }
            ]
        }

    def match(payload):
        assessments = []
        for criterion in payload["criteria"]:
            check = payload["mechanical_checks"][criterion["criterion_id"]]
            assessments.append(
                {
                    "criterion_id": criterion["criterion_id"],
                    "criterion_source_location": criterion["source_location"],
                    "clinical_status": check["clinical_status"],
                    "evidence_sufficiency": check["evidence_sufficiency"],
                    "evidence_ids": check["evidence_ids"],
                    "missing_information_ids": [],
                    "rationale": "The visible synthetic value meets the threshold.",
                    "review_flags": [],
                }
            )
        return {"assessments": assessments}

    model = ScriptedStructuredModel(
        {
            "patient_record_structurer": structure_patient,
            "trial_protocol_structurer": structure_trial,
            "matcher_judge": match,
        }
    )
    output = tmp_path / "output"
    outcome = run_challenge_screening(
        options=ChallengeRunOptions(
            topics_path=topics_path,
            output_dir=output,
            topic_ids=("S001",),
            all_topics=False,
            as_of=datetime(2026, 8, 24, tzinfo=timezone.utc),
            candidate_count=1,
            settings=EpisodeSettings(
                max_external_actions=1,
                max_selective_reviews=0,
                max_cycles=4,
            ),
            trial_protocol_cache_dir=tmp_path / "trial-cache",
        ),
        model=model,
        model_label="scripted-local",
        candidate_search=search,
        medical_disclaimer="학생 과제용 실험 결과입니다.",
        read=lambda _: pytest.fail("a resolved case must not ask a question"),
        write=lambda _: None,
    )

    assert outcome.runs[0].paused is False
    result = json.loads((output / "result.json").read_text(encoding="utf-8"))
    trial = result["screening"]["guidance"]["recommendation_views"][
        "broader_review"
    ]["trials"][0]
    assert result["run_mode"] == "challenge_topic_interactive"
    assert trial["trial_id"] == "T1"
    assert trial["recommendation_rank"] == 1
    assert trial["search_rank"] == 1
    assert result["usage"]["call_count"] == 3
    session = json.loads((output / "session.json").read_text(encoding="utf-8"))
    assert session["metadata"]["candidate_ranking"][0]["rank"] == 1
    assert session["metadata"]["trial_protocol_cache"] == {
        "reused_trial_count": 0,
        "newly_structured_trial_count": 1,
        "saved_trial_count": 1,
        "invalid_cache_file_count": 0,
        "cache_write_failure_count": 0,
    }

    second_output = tmp_path / "second-output"
    second_outcome = run_challenge_screening(
        options=ChallengeRunOptions(
            topics_path=topics_path,
            output_dir=second_output,
            topic_ids=("S001",),
            all_topics=False,
            as_of=datetime(2026, 8, 24, tzinfo=timezone.utc),
            candidate_count=1,
            settings=EpisodeSettings(
                max_external_actions=1,
                max_selective_reviews=0,
                max_cycles=4,
            ),
            trial_protocol_cache_dir=tmp_path / "trial-cache",
        ),
        model=model,
        model_label="scripted-local",
        candidate_search=search,
        medical_disclaimer="학생 과제용 실험 결과입니다.",
        read=lambda _: pytest.fail("a resolved case must not ask a question"),
        write=lambda _: None,
    )
    second_result = json.loads(
        second_outcome.runs[0].result_path.read_text(encoding="utf-8")
    )
    second_session = json.loads(
        (second_output / "session.json").read_text(encoding="utf-8")
    )
    assert second_result["usage"]["call_count"] == 2
    assert second_session["metadata"]["trial_protocol_cache"] == {
        "reused_trial_count": 1,
        "newly_structured_trial_count": 0,
        "saved_trial_count": 0,
        "invalid_cache_file_count": 0,
        "cache_write_failure_count": 0,
    }
    assert model.call_count["trial_protocol_structurer"] == 1


def test_no_related_trial_is_saved_as_a_completed_result(tmp_path: Path) -> None:
    patient_text = "The synthetic patient has pyloric stenosis."
    topics_path = tmp_path / "topics.json"
    topics_path.write_text(
        json.dumps({"topics": [{"num": "S001", "title": patient_text}]}),
        encoding="utf-8",
    )
    search = InMemoryCandidateSearch(
        [
            TrialProtocolSource(
                trial_id="T-LYMPHOMA",
                title="Follicular lymphoma study",
                conditions=["follicular lymphoma"],
                eligibility_text="Adults with lymphoma may participate.",
                source_location="synthetic:T-LYMPHOMA",
            )
        ]
    )
    model = ScriptedStructuredModel(
        {
            "patient_record_structurer": lambda _: {
                "search_conditions": [
                    {
                        "condition": "pyloric stenosis",
                        "source_quote": "pyloric stenosis",
                    }
                ],
                "facts": [
                    {
                        "fact_key": "diagnosis",
                        "statement": "The synthetic patient has pyloric stenosis.",
                        "source_quote": "The synthetic patient has pyloric stenosis.",
                    }
                ],
            }
        }
    )
    output = tmp_path / "output"
    lines: list[str] = []

    outcome = run_challenge_screening(
        options=ChallengeRunOptions(
            topics_path=topics_path,
            output_dir=output,
            topic_ids=("S001",),
            all_topics=False,
            as_of=datetime(2026, 8, 24, tzinfo=timezone.utc),
            candidate_count=5,
            settings=EpisodeSettings(
                max_external_actions=1,
                max_selective_reviews=0,
                max_cycles=3,
            ),
            trial_protocol_cache_dir=tmp_path / "trial-cache",
        ),
        model=model,
        model_label="scripted-local",
        candidate_search=search,
        medical_disclaimer="참고용 결과입니다.",
        write=lines.append,
    )

    result = json.loads(outcome.runs[0].result_path.read_text(encoding="utf-8"))
    session = json.loads((output / "session.json").read_text(encoding="utf-8"))
    assert result["status"] == "no_related_enrolling_trials"
    assert result["input"]["candidate_hits"] == []
    assert session["completed"] is True
    assert "관련된 모집 중 임상시험을 찾지 못했습니다" in "\n".join(lines)
    assert model.call_count == {"patient_record_structurer": 1}
