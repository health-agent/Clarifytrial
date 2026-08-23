"""One terminal view for search, role calls, questions, and final results."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..agents import (
    CoordinatorAgent,
    MatcherJudgeAgent,
    NextEvidenceAgent,
    SelectiveReviewerAgent,
)
from ..contracts import AgentAction, PatientState
from ..environment import (
    EnvironmentStatus,
    HiddenPatientEnvironment,
    PublicFactRequest,
    PublicQuestionCatalog,
    SyntheticInformationTools,
    ToolExecutionResult,
)
from ..llm import StructuredModel
from ..preparation import summarize_model_usage
from ..settings import EpisodeSettings
from ..trace import TraceEvent, TraceRecorder
from ..workflow import EpisodeAgents, PatientScreeningRunner
from ..workflow.patient_screening_contracts import InformationTools
from .fixtures import IntegratedUIFixture


_ROLE_LABELS = {
    "patient_record_structurer": "환자 기록 정리",
    "trial_protocol_structurer": "시험 조건 정리",
    "coordinator": "진행 관리",
    "matcher_judge": "검색·판단",
    "next_evidence": "다음 확인 문장 작성",
    "selective_reviewer": "선택 검토",
}

_ROUTE_LABELS = {
    "MATCHER_JUDGE": "확인할 조건을 검색·판단 역할에 전달",
    "NEXT_EVIDENCE": "다음에 확인할 정보를 정함",
    "SELECTIVE_REVIEWER": "근거가 약한 결론을 한 번 더 검토",
    "FINISH": "현재 실행을 종료",
}

_ACTION_LABELS = {
    "ASK_PATIENT": "환자에게 질문",
    "LOOKUP_RECORD": "기존 기록 확인",
    "REQUEST_VERIFICATION": "기존 공식 결과 확인",
}

_DECISION_LABELS = {
    ("retain", "confirmed"): "후보 유지 / 현재 자료로 조건 확인 완료",
    ("retain", "not_confirmed"): "후보 유지 / 추가 확인 필요",
    ("retain", "uncertain"): "후보 유지 / 판단 보류",
    ("remove", "ineligible"): "후보 제외 / 참가 조건 불충족",
    ("uncertain", "uncertain"): "후보 판단 보류",
}

_STOP_LABELS = {
    "all_trials_resolved": "모든 후보 시험의 현재 판단이 끝남",
    "no_pending_information": "현재 방법으로 더 확인할 정보가 없음",
    "action_limit": "정해 둔 확인 횟수를 모두 사용함",
    "awaiting_patient_choice": "환자의 선택을 기다림",
    "awaiting_clinician_authorization": "담당자의 승인을 기다림",
    "deferred": "지금 확인할 수 없어 보류함",
    "human_review": "사람이 다시 볼 결론이 남음",
    "tool_returned_no_information": "선택한 경로에서 정보를 얻지 못함",
    "cycle_limit": "정해 둔 진행 횟수에 도달함",
}


def _line(char: str = "=") -> str:
    return char * 72


def _decision_label(decision: Mapping[str, Any]) -> str:
    key = (str(decision["candidate_status"]), str(decision["confirmation_status"]))
    return _DECISION_LABELS.get(key, f"{key[0]} / {key[1]}")


class IntegratedTerminalRenderer:
    """Render only observable workflow events, without model chain of thought."""

    def __init__(
        self,
        *,
        fixture: IntegratedUIFixture,
        model_label: str,
        write: Callable[[str], None],
    ) -> None:
        self.fixture = fixture
        self.model_label = model_label
        self.write = write
        self.titles = {item.trial_id: item.title for item in fixture.trial_sources}
        self.candidate_ids: list[str] = []
        self.structured_trials: set[str] = set()
        self.judged_trials: set[str] = set()
        self.judgment_started = False
        self.information_started = False

    def start(self) -> None:
        case = self.fixture.screening_case
        self.write(_line())
        self.write("ClarifyTrial 전체 경로 실행")
        self.write(_line())
        self.write(f"합성 환자: {case.initial_patient_state.patient_id}")
        self.write(f"검색 대상: 공개 임상시험 {len(self.fixture.trial_sources)}개")
        self.write(f"가져올 후보: {len(self.fixture.candidate_hits)}개")
        self.write(f"모델: {self.model_label}")
        self.write("")
        self.write("[1/6] 표준 JSON 환자 상태 읽기")

    def on_event(self, event: TraceEvent) -> None:
        output = event.output
        if event.actor == "structured_patient_input":
            fact_ids = output.get("evidence_ids", [])
            missing_ids = output.get("missing_fact_ids", [])
            self.write(f"  현재 확인된 사실: {len(fact_ids)}개")
            self.write(f"  아직 확인할 사실: {len(missing_ids)}개")
            return
        if event.event == "candidate_trials_selected":
            self.candidate_ids = [str(item) for item in output.get("trial_ids", [])]
            scores = list(output.get("scores", []))
            self.write("")
            self.write("[2/6] 관련 시험 검색 완료")
            self.write(
                f"  전체 {len(self.fixture.trial_sources)}개 중 "
                f"후보 {len(self.candidate_ids)}개를 골랐습니다."
            )
            self.write("  검색 방법: 질환이 같은 시험을 먼저 고른 뒤 문구가 가까운 순서로 정렬")
            for index, trial_id in enumerate(self.candidate_ids, start=1):
                score = scores[index - 1] if index - 1 < len(scores) else None
                score_text = "" if score is None else f" · 검색점수 {float(score):.4f}"
                title = self.titles.get(trial_id, trial_id)
                self.write(f"  {index}. {trial_id} · {title}{score_text}")
            self.write("")
            self.write("[3/6] 저장된 시험 조건 불러오기")
            return
        if event.actor == "structured_trial_store":
            trial_id = str(event.input_refs[0]) if event.input_refs else "알 수 없는 시험"
            self.structured_trials.add(trial_id)
            criterion_count = len(output.get("criterion_ids", []))
            need_count = int(output.get("information_need_count", 0))
            total = len(self.candidate_ids) or len(self.fixture.candidate_hits)
            self.write(
                f"  [{len(self.structured_trials)}/{total}] {trial_id}: "
                f"조건 {criterion_count}개, 확인할 정보 {need_count}개"
            )
            return
        if event.actor == "coordinator" and event.event == "structured_model_completed":
            response = output.get("response", {})
            route = str(response.get("route", ""))
            if route:
                if route == "MATCHER_JUDGE" and not self.judgment_started:
                    self.write("")
                    self.write("[4/6] 조건별 판단")
                    self.judgment_started = True
                self.write(f"  진행 관리: {_ROUTE_LABELS.get(route, route)}")
            return
        if event.actor == "coordinator_rules" and event.event == "single_route_selected":
            response = output.get("response", {})
            route = str(response.get("route", ""))
            if route:
                if route == "MATCHER_JUDGE" and not self.judgment_started:
                    self.write("")
                    self.write("[4/6] 조건별 판단")
                    self.judgment_started = True
                self.write(f"  진행 규칙: {_ROUTE_LABELS.get(route, route)}")
            return
        if event.actor == "matcher_judge" and event.event == "structured_model_completed":
            response = output.get("response", {})
            assessments = list(response.get("assessments", []))
            if not assessments:
                return
            counts: dict[str, int] = {}
            for assessment in assessments:
                criterion_id = str(assessment.get("criterion_id", ""))
                trial_id = criterion_id.split(":", 1)[0]
                counts[trial_id] = counts.get(trial_id, 0) + 1
            for trial_id, count in sorted(counts.items()):
                verb = "재판정" if trial_id in self.judged_trials else "첫 판단"
                self.judged_trials.add(trial_id)
                self.write(f"  검색·판단: {trial_id} 조건 {count}개 {verb} 완료")
            return
        if event.actor == "selective_reviewer" and event.event == "structured_model_completed":
            response = output.get("response", {})
            self.write(
                "  선택 검토: "
                f"{response.get('conclusion_id', '')} → {response.get('decision', '')}"
            )
            return
        if event.actor == "information_planning_rules":
            reason = str(output.get("selection_reason", ""))
            if not self.information_started:
                self.write("")
                self.write("[5/6] 부족한 정보 확인")
                self.information_started = True
            if reason:
                self.write(f"  선택 이유: {reason}")

    def show_action(self, action: AgentAction) -> None:
        self.write(f"  확인 내용: {action.message or action.reason}")
        self.write(f"  확인 방법: {_ACTION_LABELS.get(action.action.value, action.action.value)}")
        self.write(f"  영향을 받는 조건: {len(action.related_criterion_ids)}개")

    def show_tool_result(self, result: ToolExecutionResult) -> None:
        if result.status is EnvironmentStatus.REVEALED:
            for fact in result.new_facts:
                self.write(f"  확인된 합성 답변: {fact.statement}")
        else:
            self.write("  이 경로에서는 정보를 얻지 못했습니다.")

    def finish(
        self,
        *,
        result: Mapping[str, Any],
        result_path: Path,
        trace_path: Path,
        medical_disclaimer: str,
    ) -> None:
        screening = result["screening"]
        self.write("")
        if not self.information_started:
            self.write("[5/6] 부족한 정보 확인")
            self.write("  추가로 확인할 정보가 없었습니다.")
            self.write("")
        self.write("판정 변화 요약")
        previous: dict[str, str] = {}
        for snapshot in screening["decision_history"]:
            current = {
                str(item["trial_id"]): _decision_label(item)
                for item in snapshot["decisions"]
            }
            changes = [
                (trial_id, previous.get(trial_id), label)
                for trial_id, label in current.items()
                if previous.get(trial_id) != label
            ]
            if changes:
                self.write(f"  {snapshot['reason']}")
                for trial_id, before, after in changes:
                    if before is None:
                        self.write(f"  - {trial_id}: {after}")
                    else:
                        self.write(f"  - {trial_id}: {before} → {after}")
            previous = current

        self.write("")
        self.write("[6/6] 최종 결과")
        stop_reason = str(screening["stop_reason"])
        self.write(f"  종료 이유: {_STOP_LABELS.get(stop_reason, stop_reason)}")
        revealed_fact_ids = {
            str(item["tool_result"].get("target_fact_id"))
            for item in screening.get("action_history", [])
            if item["tool_result"].get("status") == "revealed"
        }
        if screening.get("action_history"):
            self.write(f"  실행한 확인: {len(screening['action_history'])}개")
        for decision in screening["final_decisions"]:
            trial_id = str(decision["trial_id"])
            self.write(f"  - {trial_id} · {self.titles.get(trial_id, trial_id)}")
            self.write(f"    {_decision_label(decision)}")
            if decision["candidate_status"] == "remove":
                continue
            pending = list(decision.get("pending_information", []))
            for item in pending:
                if str(item["fact_id"]) in revealed_fact_ids:
                    self.write(
                        "    확인했지만 판단이 끝나지 않은 정보: "
                        f"{item['description']}"
                    )
                else:
                    self.write(f"    아직 확인하지 못한 정보: {item['description']}")

        usage = result["usage"]
        self.write("")
        self.write("모델 호출")
        for role, item in usage["by_role"].items():
            self.write(
                f"  - {_ROLE_LABELS.get(role, role)}: "
                f"{item['call_count']}회, {item['total_tokens']:,}토큰"
            )
        self.write(f"  전체: {usage['call_count']}회, {usage['total_tokens']:,}토큰")
        self.write("")
        self.write(f"결과 파일: {result_path}")
        self.write(f"단계별 기록: {trace_path}")
        self.write(medical_disclaimer)


class TerminalTraceRecorder(TraceRecorder):
    """Forward each stored trace event to the terminal renderer."""

    def __init__(self, case_id: str, renderer: IntegratedTerminalRenderer) -> None:
        super().__init__(case_id)
        self._renderer = renderer

    def record(self, **kwargs: Any) -> TraceEvent:
        event = super().record(**kwargs)
        self._renderer.on_event(event)
        return event


class PausingInformationTools:
    """Pause before a synthetic answer is opened, then show only the returned fact."""

    def __init__(
        self,
        delegate: InformationTools,
        renderer: IntegratedTerminalRenderer,
        *,
        auto_advance: bool,
        read: Callable[[str], str],
    ) -> None:
        self._delegate = delegate
        self._renderer = renderer
        self._auto_advance = auto_advance
        self._read = read

    def execute(
        self,
        agent_action: AgentAction,
        patient_state: PatientState,
    ) -> ToolExecutionResult:
        self._renderer.show_action(agent_action)
        if not self._auto_advance:
            self._read("  미리 정해 둔 합성 답변을 적용하려면 Enter를 누르세요. ")
        result = self._delegate.execute(agent_action, patient_state)
        self._renderer.show_tool_result(result)
        return result


def run_integrated_terminal_ui(
    *,
    fixture: IntegratedUIFixture,
    model: StructuredModel,
    model_label: str,
    settings: EpisodeSettings,
    output_dir: str | Path,
    medical_disclaimer: str,
    auto_advance: bool = False,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> Path:
    """Run the complete synthetic path and render its observable stages."""

    if write is print and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    renderer = IntegratedTerminalRenderer(
        fixture=fixture,
        model_label=model_label,
        write=write,
    )
    renderer.start()
    agents = EpisodeAgents(
        coordinator=CoordinatorAgent(model),
        matcher_judge=MatcherJudgeAgent(model),
        next_evidence=NextEvidenceAgent(model),
        selective_reviewer=SelectiveReviewerAgent(model),
    )
    case = fixture.screening_case
    trace = TerminalTraceRecorder(case.case_id, renderer)
    trace.record(
        cycle=0,
        actor="structured_patient_input",
        event="patient_json_loaded",
        input_refs=[case.initial_patient_state.patient_id],
        output={
            "evidence_ids": [
                item.evidence_id for item in case.initial_patient_state.facts
            ],
            "missing_fact_ids": [item.fact_id for item in case.evidence_requests],
        },
    )
    trace.record(
        cycle=0,
        actor="candidate_trial_search",
        event="candidate_trials_selected",
        input_refs=list(fixture.search_conditions),
        output={
            "search_conditions": list(fixture.search_conditions),
            "trial_ids": [item.source.trial_id for item in fixture.candidate_hits],
            "scores": [item.score for item in fixture.candidate_hits],
            "methods": sorted(
                {item.retrieval_method for item in fixture.candidate_hits}
            ),
        },
    )
    for trial in case.trials:
        criterion_ids = [item.criterion_id for item in trial.criteria]
        need_count = sum(
            bool(set(request.related_criterion_ids) & set(criterion_ids))
            for request in case.evidence_requests
        )
        trace.record(
            cycle=0,
            actor="structured_trial_store",
            event="trial_conditions_loaded",
            input_refs=[trial.trial_id],
            output={
                "criterion_ids": criterion_ids,
                "information_need_count": need_count,
            },
        )
    public_requests = [
        PublicFactRequest(
            fact_id=item.fact_id,
            description=item.description,
            available_actions=tuple(item.acceptable_actions),
        )
        for item in case.evidence_requests
    ]
    base_tools = SyntheticInformationTools(
        PublicQuestionCatalog(public_requests),
        HiddenPatientEnvironment(fixture.hidden_answers),
    )
    tools = PausingInformationTools(
        base_tools,
        renderer,
        auto_advance=auto_advance,
        read=read,
    )
    screening = PatientScreeningRunner(agents, settings).run(
        case,
        tools,
        trace=trace,
    )
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    result_path = destination / "result.json"
    trace_path = destination / "trace.jsonl"
    result_document = {
        "prepared": {
            "search_conditions": list(fixture.search_conditions),
            "candidate_hits": [
                item.model_dump(mode="json") for item in fixture.candidate_hits
            ],
            "screening_case": case.model_dump(mode="json"),
        },
        "screening": screening.model_dump(mode="json"),
        "usage": summarize_model_usage(trace).model_dump(mode="json"),
    }
    payload = {
        "run_mode": "integrated_terminal_ui_synthetic_evaluation",
        "medical_disclaimer": medical_disclaimer,
        "input": {
            "patient_id": case.initial_patient_state.patient_id,
            "trial_search_pool_count": len(fixture.trial_sources),
            "trial_set": fixture.trial_set_path,
            "patient_pairs": fixture.patient_pairs_path,
            "generation_config": fixture.generation_config_path,
            "hidden_answers_opened_only_after_action": True,
        },
        "result": result_document,
    }
    result_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    trace.write_jsonl(trace_path)
    renderer.finish(
        result=result_document,
        result_path=result_path,
        trace_path=trace_path,
        medical_disclaimer=medical_disclaimer,
    )
    return result_path
