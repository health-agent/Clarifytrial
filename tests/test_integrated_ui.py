from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from clarifytrial.cli import main
from clarifytrial.contracts import (
    AgentAction,
    EvidenceFact,
    EvidenceSourceType,
    NextAction,
    PatientState,
    VerificationStatus,
)
from clarifytrial.environment import EnvironmentStatus, ToolExecutionResult
from clarifytrial.settings import EpisodeSettings
from clarifytrial.ui import build_integrated_ui_fixture
from clarifytrial.ui.terminal import (
    IntegratedTerminalRenderer,
    PausingInformationTools,
    TerminalTraceRecorder,
    run_integrated_terminal_ui,
)


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _fixture(patient_id: str = "natural-type_2_diabetes-11"):
    return build_integrated_ui_fixture(
        trial_set_path=(
            REPOSITORY_ROOT
            / "data"
            / "natural_evaluation_v1"
            / "preliminary_trial_set.json"
        ),
        patient_pairs_path=(
            REPOSITORY_ROOT
            / "data"
            / "natural_evaluation_v2"
            / "preliminary_patient_pairs.json"
        ),
        generation_config_path=(
            REPOSITORY_ROOT
            / "configs"
            / "natural_evaluation_patient_generation_v2.json"
        ),
        patient_id=patient_id,
    )


def test_integrated_fixture_searches_all_15_trials_and_hides_five_answers() -> None:
    fixture = _fixture()

    assert len(fixture.trial_sources) == 15
    assert len(fixture.candidate_hits) == 5
    assert len(fixture.hidden_answers) == 5
    assert {item.source.trial_id for item in fixture.candidate_hits} == set(
        fixture.expected_candidate_trial_ids
    )
    visible_evidence = {
        item.evidence_id
        for item in fixture.screening_case.initial_patient_state.facts
    }
    hidden_evidence = {item.evidence.evidence_id for item in fixture.hidden_answers}
    assert visible_evidence.isdisjoint(hidden_evidence)
    assert len(fixture.screening_case.evidence_requests) == 5


def test_integrated_fixture_supports_each_disease_group() -> None:
    fixtures = [
        _fixture("natural-type_2_diabetes-11"),
        _fixture("natural-breast_cancer-11"),
        _fixture("natural-major_depressive_disorder-11"),
    ]

    assert {
        item.screening_case.initial_patient_state.patient_id for item in fixtures
    } == {
        "natural-type_2_diabetes-11",
        "natural-breast_cancer-11",
        "natural-major_depressive_disorder-11",
    }
    assert all(len(item.trial_sources) == 15 for item in fixtures)
    assert all(len(item.hidden_answers) == 5 for item in fixtures)


def test_integrated_fixture_can_start_from_a_larger_public_search_pool(
    tmp_path: Path,
) -> None:
    local_fixture = _fixture()
    corpus = tmp_path / "trials.jsonl"
    corpus.write_text(
        "\n".join(
            json.dumps(
                {
                    "nct_id": source.trial_id,
                    "title": source.title,
                    "conditions": source.conditions,
                    "brief_summary": source.summary,
                    "eligibility_text": source.eligibility_text,
                    "overall_status": "RECRUITING",
                },
                ensure_ascii=False,
            )
            for source in local_fixture.trial_sources
        )
        + "\n",
        encoding="utf-8",
    )

    fixture = build_integrated_ui_fixture(
        trial_set_path=(
            REPOSITORY_ROOT
            / "data"
            / "natural_evaluation_v1"
            / "preliminary_trial_set.json"
        ),
        patient_pairs_path=(
            REPOSITORY_ROOT
            / "data"
            / "natural_evaluation_v2"
            / "preliminary_patient_pairs.json"
        ),
        generation_config_path=(
            REPOSITORY_ROOT
            / "configs"
            / "natural_evaluation_patient_generation_v2.json"
        ),
        patient_id="natural-type_2_diabetes-11",
        broad_corpus_path=corpus,
        broad_search_top_k=15,
    )

    assert fixture.search_pool_count == 15
    assert fixture.search_top_k == 15
    assert fixture.search_scope_label == "모집 중·모집 예정 공개 임상시험"
    assert {item.source.trial_id for item in fixture.candidate_hits} == set(
        fixture.expected_candidate_trial_ids
    )
    assert fixture.screening_case.candidate_ranking


def test_integrated_fixture_uses_the_same_aliases_and_categorical_rules_as_evaluation() -> None:
    fixture = _fixture("natural-type_2_diabetes-11")
    criteria = {
        item.criterion_id: item
        for trial in fixture.screening_case.trials
        for item in trial.criteria
    }

    bmi = criteria["NCT06897475:candidate:003:annotation:01"]
    diagnosis = criteria["NCT06897475:candidate:001:annotation:01"]
    assert bmi.numeric_constraint.concept == "type_2_diabetes:body_mass_index"
    assert diagnosis.numeric_constraint.operator.value == "eq"
    assert diagnosis.numeric_constraint.unit == "bool"
    assert diagnosis.evidence_requirement is not None


def test_terminal_renderer_shows_every_stage_without_private_reasoning(
    tmp_path: Path,
) -> None:
    fixture = _fixture()
    lines: list[str] = []
    renderer = IntegratedTerminalRenderer(
        fixture=fixture,
        model_label="test-model / medium",
        write=lines.append,
    )
    trace = TerminalTraceRecorder(fixture.screening_case.case_id, renderer)

    renderer.start()
    trace.record(
        cycle=0,
        actor="structured_patient_input",
        event="patient_json_loaded",
        output={
            "evidence_ids": ["fact-1", "fact-2"],
            "missing_fact_ids": ["missing-1"],
        },
    )
    candidate_ids = list(fixture.expected_candidate_trial_ids)
    trace.record(
        cycle=0,
        actor="candidate_trial_search",
        event="candidate_trials_selected",
        output={"trial_ids": candidate_ids, "scores": [1, 0.9, 0.8, 0.7, 0.6]},
    )
    trace.record(
        cycle=0,
        actor="structured_trial_store",
        event="trial_conditions_loaded",
        input_refs=[candidate_ids[0]],
        output={"criterion_ids": ["criterion-1"], "information_need_count": 1},
    )
    trace.record(
        cycle=0,
        actor="coordinator",
        event="structured_model_completed",
        output={"response": {"route": "MATCHER_JUDGE"}},
    )
    trace.record(
        cycle=0,
        actor="mechanical_checks",
        event="structured_criteria_applied_without_model",
        input_refs=[f"{candidate_ids[0]}:inclusion:001"],
        output={
            "criterion_count": 1,
            "criterion_ids": [f"{candidate_ids[0]}:inclusion:001"],
        },
    )
    trace.record(
        cycle=0,
        actor="matcher_judge",
        event="structured_model_completed",
        output={
            "response": {
                "assessments": [{"criterion_id": f"{candidate_ids[0]}:inclusion:001"}]
            }
        },
    )
    trace.record(
        cycle=1,
        actor="information_planning_rules",
        event="acquisition_path_selected",
        output={"selection_reason": "여러 시험에 함께 필요한 정보부터 확인"},
    )
    decision = {
        "trial_id": candidate_ids[0],
        "candidate_status": "retain",
        "confirmation_status": "not_confirmed",
        "pending_information": [
            {"fact_id": "missing-1", "description": "최근 검사 결과"}
        ],
    }
    renderer.finish(
        result={
            "screening": {
                "stop_reason": "action_limit",
                "decision_history": [
                    {
                        "reason": "조건 판단과 상태 집계",
                        "decisions": [decision],
                    }
                ],
                "final_decisions": [decision],
            },
            "usage": {
                "call_count": 2,
                "total_tokens": 1234,
                "by_role": {
                    "coordinator": {"call_count": 1, "total_tokens": 600},
                    "matcher_judge": {"call_count": 1, "total_tokens": 634},
                },
            },
        },
        result_path=tmp_path / "result.json",
        trace_path=tmp_path / "trace.jsonl",
        medical_disclaimer="학생 과제용 실험 결과입니다.",
    )

    text = "\n".join(lines)
    for stage in range(1, 7):
        assert f"[{stage}/6]" in text
    assert "전체 15개 중 후보 5개" in text
    assert "시험의 모든 참가 조건을 옮긴 자료는 아닙니다" in text
    assert "진행 관리:" in text
    assert "조건 판단:" in text
    assert "코드 판단: 구조화 조건 1개 처리 · 모델 호출 없음" in text
    assert "판정 변화 요약" in text
    assert "전체 외부 모델 호출: 2회, 1,234토큰" in text
    assert "chain of thought" not in text.casefold()


class _OneFactTools:
    def __init__(self, result: ToolExecutionResult) -> None:
        self.result = result
        self.called = False

    def execute(
        self,
        agent_action: AgentAction,
        patient_state: PatientState,
    ) -> ToolExecutionResult:
        self.called = True
        return self.result


def test_terminal_pauses_before_opening_the_synthetic_answer() -> None:
    fixture = _fixture()
    lines: list[str] = []
    prompts: list[str] = []
    renderer = IntegratedTerminalRenderer(
        fixture=fixture,
        model_label="test-model",
        write=lines.append,
    )
    state = PatientState(
        patient_id="synthetic-patient",
        as_of=datetime(2026, 8, 21, tzinfo=timezone.utc),
        facts=[],
    )
    fact = EvidenceFact(
        evidence_id="answer-1",
        statement="합성 환자의 나이는 55세다.",
        source_type=EvidenceSourceType.MEDICAL_RECORD,
        source_location="synthetic-record#age",
        event_date=date(2026, 8, 20),
        recorded_date=date(2026, 8, 21),
        verification_status=VerificationStatus.VERIFIED,
        concept="age",
        value=55,
        unit="years",
    )
    result = ToolExecutionResult(
        action=NextAction.LOOKUP_RECORD,
        target_fact_id="missing:age",
        status=EnvironmentStatus.REVEALED,
        new_facts=[fact],
        patient_state=state.model_copy(update={"facts": [fact]}),
    )
    delegate = _OneFactTools(result)

    def read_answer(prompt: str) -> str:
        prompts.append(prompt)
        assert delegate.called is False
        return ""

    tools = PausingInformationTools(
        delegate,
        renderer,
        auto_advance=False,
        read=read_answer,
    )
    returned = tools.execute(
        AgentAction(
            action=NextAction.LOOKUP_RECORD,
            target_fact_id="missing:age",
            related_criterion_ids=["trial:criterion:1"],
            reason="나이를 확인한다.",
        ),
        state,
    )

    assert delegate.called is True
    assert returned.new_facts == [fact]
    assert len(prompts) == 1
    assert "Enter" in prompts[0]
    assert "합성 환자의 나이는 55세다." in "\n".join(lines)


def test_full_ui_live_model_requires_confirmation() -> None:
    with pytest.raises(SystemExit) as captured:
        main(["run-full-ui", "--provider", "codex-subscription"])

    assert captured.value.code == 2


def test_full_ui_default_runs_offline_on_current_public_dataset(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "execution-error.json").write_text(
        '{"status": "older failure"}\n',
        encoding="utf-8",
    )
    exit_code = main(
        [
            "run-full-ui",
            "--output",
            str(tmp_path),
            "--auto",
        ]
    )

    assert exit_code == 0
    assert not (tmp_path / "execution-error.json").exists()
    result = (tmp_path / "result.json").read_text(encoding="utf-8")
    assert '"run_mode": "integrated_terminal_ui_synthetic_evaluation"' in result
    assert '"patient_id": "source-chronic_pancreatitis-04"' in result
    output = capsys.readouterr().out
    assert "코드 역할 단계" in output
    assert "외부 모델 호출: 0회, 0토큰" in output
    assert "모델 호출\n" not in output


class _ExecutionFailureModel:
    def complete(self, call):
        raise RuntimeError("synthetic provider failure")


class _ResponseParsingFailureModel:
    def complete(self, call):
        call.response_model.model_validate({})
        raise AssertionError("model validation should fail")


@pytest.mark.parametrize(
    ("model", "expected_error_type", "expected_exception"),
    [
        (_ExecutionFailureModel(), "RuntimeError", RuntimeError),
        (_ResponseParsingFailureModel(), "ValidationError", ValidationError),
    ],
)
def test_integrated_ui_persists_model_failure_before_reraising(
    tmp_path: Path,
    model,
    expected_error_type: str,
    expected_exception: type[Exception],
) -> None:
    fixture = _fixture()
    with pytest.raises(expected_exception):
        run_integrated_terminal_ui(
            fixture=fixture,
            model=model,
            model_label="failing-test-model",
            settings=EpisodeSettings(
                max_external_actions=1,
                max_selective_reviews=0,
                max_cycles=3,
            ),
            output_dir=tmp_path,
            medical_disclaimer="synthetic test only",
            auto_advance=True,
            write=lambda _: None,
        )

    assert not (tmp_path / "result.json").exists()
    failure = json.loads(
        (tmp_path / "execution-error.json").read_text(encoding="utf-8")
    )
    assert failure == {
        "status": "screening_execution_failed",
        "phase": "screening_workflow",
        "case_id": fixture.screening_case.case_id,
        "model": "failing-test-model",
        "error": {
            "type": expected_error_type,
            "message": failure["error"]["message"],
        },
    }
    assert failure["error"]["message"]

    trace_events = [
        json.loads(line)
        for line in (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    last_event = trace_events[-1]
    assert last_event["actor"] == "screening_workflow"
    assert last_event["event"] == "execution_failed"
    assert last_event["output"] == failure


def test_integrated_ui_failure_does_not_leave_an_older_success_result(
    tmp_path: Path,
) -> None:
    (tmp_path / "result.json").write_text(
        '{"status": "older success"}\n',
        encoding="utf-8",
    )
    (tmp_path / "execution-error.json").write_text(
        '{"status": "older failure"}\n',
        encoding="utf-8",
    )
    (tmp_path / "trace.jsonl").write_text(
        '{"event": "older run"}\n',
        encoding="utf-8",
    )

    fixture = _fixture()
    with pytest.raises(RuntimeError, match="synthetic provider failure"):
        run_integrated_terminal_ui(
            fixture=fixture,
            model=_ExecutionFailureModel(),
            model_label="failing-test-model",
            settings=EpisodeSettings(
                max_external_actions=1,
                max_selective_reviews=0,
                max_cycles=3,
            ),
            output_dir=tmp_path,
            medical_disclaimer="synthetic test only",
            auto_advance=True,
            write=lambda _: None,
        )

    assert not (tmp_path / "result.json").exists()
    failure = json.loads(
        (tmp_path / "execution-error.json").read_text(encoding="utf-8")
    )
    assert failure["status"] == "screening_execution_failed"
    trace_events = [
        json.loads(line)
        for line in (tmp_path / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert all(event.get("event") != "older run" for event in trace_events)
    assert trace_events[-1]["event"] == "execution_failed"
