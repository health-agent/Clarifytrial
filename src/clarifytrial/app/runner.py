"""Run the complete structured workflow on arbitrary supplied JSON files."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..agents import (
    CoordinatorAgent,
    MatcherJudgeAgent,
    NextEvidenceAgent,
    SelectiveReviewerAgent,
)
from ..environment import (
    HiddenFactAnswer,
    HiddenPatientEnvironment,
    PublicFactRequest,
    PublicQuestionCatalog,
    SyntheticInformationTools,
)
from ..llm import StructuredModel
from ..preparation import summarize_model_usage
from ..settings import EpisodeSettings
from ..trace import TraceRecorder
from ..workflow import EpisodeAgents, PatientScreeningRunner
from .contracts import ScreeningSession
from .loaders import (
    load_general_patient,
    load_structured_trials,
    prepare_general_case,
)
from .tools import (
    InteractiveInformationTools,
    InteractiveSessionPaused,
    SessionStore,
)


@dataclass(frozen=True, slots=True)
class GeneralRunOptions:
    patient_path: Path
    trials_path: Path
    output_dir: Path
    settings: EpisodeSettings
    answers_path: Path | None = None
    resume_path: Path | None = None


@dataclass(frozen=True, slots=True)
class GeneralRunOutcome:
    result_path: Path | None
    trace_path: Path
    session_path: Path
    paused: bool


def _read_answers(path: Path) -> list[HiddenFactAnswer]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("answers")
    if not isinstance(raw, list):
        raise ValueError("answer JSON must be a list or an object with answers")
    return [HiddenFactAnswer.model_validate(item) for item in raw]


def _agents(model: StructuredModel) -> EpisodeAgents:
    return EpisodeAgents(
        coordinator=CoordinatorAgent(model),
        matcher_judge=MatcherJudgeAgent(model),
        next_evidence=NextEvidenceAgent(model),
        selective_reviewer=SelectiveReviewerAgent(model),
    )


def _show_final(result: dict, write: Callable[[str], None]) -> None:
    screening = result["screening"]
    views = screening["guidance"]["recommendation_views"]
    write("")
    write("최종 결과")
    for key in ("current_evidence", "broader_review"):
        view = views[key]
        write(f"- {view['title']}: {len(view['trials'])}개")
        for trial in view["trials"]:
            write(f"  · {trial['trial_id']}: {trial['status_label']}")
    removed = [
        item
        for item in screening["final_decisions"]
        if item["candidate_status"] == "remove"
    ]
    write(f"- 현재 제외되는 시험: {len(removed)}개")
    write(f"- 종료 이유: {screening['stop_reason']}")
    usage = result["usage"]
    write(f"- 모델 호출: {usage['call_count']}회, {usage['total_tokens']:,}토큰")


def run_general_screening(
    *,
    options: GeneralRunOptions,
    model: StructuredModel,
    model_label: str,
    medical_disclaimer: str,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> GeneralRunOutcome:
    patient = load_general_patient(options.patient_path)
    sources = load_structured_trials(options.trials_path)
    prepared = prepare_general_case(patient, sources)
    options.output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = options.output_dir / "trace.jsonl"
    result_path = options.output_dir / "result.json"
    session_path = options.resume_path or options.output_dir / "session.json"

    if options.resume_path is not None:
        store = SessionStore.load(options.resume_path)
        if store.session.case_id != prepared.case.case_id:
            raise ValueError("resume session belongs to a different case_id")
    else:
        store = SessionStore(
            session_path,
            ScreeningSession(
                case_id=prepared.case.case_id,
                patient_state=prepared.case.initial_patient_state,
                metadata={
                    "patient_path": str(options.patient_path),
                    "trials_path": str(options.trials_path),
                    "model": model_label,
                    "action_budget": options.settings.max_external_actions,
                },
            ),
        )
        store.save()

    case = prepared.case.model_copy(
        update={"initial_patient_state": store.session.patient_state}
    )
    previous_action_count = store.session.action_count
    remaining_actions = max(
        0,
        options.settings.max_external_actions - store.session.action_count,
    )
    run_settings = options.settings.model_copy(
        update={"max_external_actions": remaining_actions}
    )
    recorder = TraceRecorder(case.case_id)
    recorder.record(
        cycle=0,
        actor="candidate_trial_search",
        event="candidate_trials_selected",
        input_refs=patient.search_conditions,
        output={
            "trial_pool_count": prepared.trial_pool_count,
            "trial_ids": [item.source.trial_id for item in prepared.candidate_hits],
            "scores": [item.score for item in prepared.candidate_hits],
            "method": "local-bm25-structured-trial-pool",
        },
    )
    write("ClarifyTrial 범용 실행")
    write(f"환자: {case.initial_patient_state.patient_id}")
    write(
        f"시험 문서 {prepared.trial_pool_count}개 중 "
        f"후보 {len(case.trials)}개를 선택했습니다."
    )
    write(f"남은 확인 횟수: {remaining_actions}회")

    if options.answers_path is not None:
        public_requests = [
            PublicFactRequest(
                fact_id=item.fact_id,
                description=item.description,
                available_actions=tuple(item.acceptable_actions),
            )
            for item in case.evidence_requests
        ]
        tools = SyntheticInformationTools(
            PublicQuestionCatalog(public_requests),
            HiddenPatientEnvironment(_read_answers(options.answers_path)),
        )
    else:
        tools = InteractiveInformationTools(store, read=read, write=write)

    try:
        screening = PatientScreeningRunner(_agents(model), run_settings).run(
            case,
            tools,
            trace=recorder,
            initial_revealed_fact_ids=set(store.session.revealed_fact_ids),
        )
    except InteractiveSessionPaused:
        recorder.write_jsonl(trace_path)
        write(f"진행 상태를 저장했습니다: {session_path}")
        return GeneralRunOutcome(
            result_path=None,
            trace_path=trace_path,
            session_path=session_path,
            paused=True,
        )

    usage = summarize_model_usage(recorder)
    result_document = {
        "run_mode": (
            "general_structured_synthetic_answers"
            if options.answers_path is not None
            else "general_structured_interactive"
        ),
        "model": model_label,
        "input": {
            "patient_path": str(options.patient_path),
            "trials_path": str(options.trials_path),
            "trial_pool_count": prepared.trial_pool_count,
            "candidate_hits": [
                item.model_dump(mode="json") for item in prepared.candidate_hits
            ],
            "resumed": options.resume_path is not None,
            "previous_action_count": previous_action_count,
        },
        "screening": screening.model_dump(mode="json"),
        "usage": usage.model_dump(mode="json"),
        "medical_disclaimer": medical_disclaimer,
    }
    result_path.write_text(
        json.dumps(result_document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    recorder.write_jsonl(trace_path)
    revealed_after_run = list(store.session.revealed_fact_ids)
    for item in screening.action_history:
        fact_id = item.agent_action.target_fact_id
        if (
            item.tool_result.status.value == "revealed"
            and fact_id is not None
            and fact_id not in revealed_after_run
        ):
            revealed_after_run.append(fact_id)
    store.session = store.session.model_copy(
        update={
            "patient_state": screening.final_patient_state,
            "revealed_fact_ids": revealed_after_run,
            "action_count": max(
                store.session.action_count,
                previous_action_count + len(screening.action_history),
            ),
            "completed": True,
            "result": screening,
        }
    )
    store.save()
    _show_final(result_document, write)
    write(f"결과 파일: {result_path}")
    write(medical_disclaimer)
    return GeneralRunOutcome(
        result_path=result_path,
        trace_path=trace_path,
        session_path=session_path,
        paused=False,
    )


__all__ = ["GeneralRunOptions", "GeneralRunOutcome", "run_general_screening"]
