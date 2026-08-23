from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

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
from clarifytrial.ui import build_integrated_ui_fixture
from clarifytrial.ui.terminal import (
    IntegratedTerminalRenderer,
    PausingInformationTools,
    TerminalTraceRecorder,
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
    assert "진행 관리:" in text
    assert "검색·판단:" in text
    assert "판정 변화 요약" in text
    assert "전체: 2회, 1,234토큰" in text
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


def test_full_ui_command_requires_live_model_confirmation() -> None:
    with pytest.raises(SystemExit) as captured:
        main(["run-full-ui"])

    assert captured.value.code == 2
