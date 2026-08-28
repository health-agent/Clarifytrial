"""Run the complete structured workflow on arbitrary supplied JSON files."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

from ..agents import (
    CoordinatorAgent,
    MatcherJudgeAgent,
    NextEvidenceAgent,
    SelectiveReviewerAgent,
)
from ..contracts import (
    EvidenceCaptureMethod,
    EvidenceInputProvenance,
    TrialSearchRank,
)
from ..environment import (
    EnvironmentStatus,
    HiddenFactAnswer,
    HiddenPatientEnvironment,
    PublicFactRequest,
    PublicQuestionCatalog,
    SyntheticInformationTools,
)
from ..llm import StructuredModel
from ..io import atomic_write_text
from ..preparation import CandidateSearch, summarize_model_usage
from ..reporting import build_terminal_summary_lines
from ..settings import EpisodeSettings
from ..trace import TraceRecorder
from ..workflow import (
    EpisodeAgents,
    PatientScreeningResult,
    PatientScreeningRunner,
    PatientScreeningStopReason,
)
from .contracts import GeneralPatientInput, ScreeningSession, StructuredTrialSource
from .loaders import (
    PreparedGeneralCase,
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
    retry_unavailable: bool = False
    approve_patient_choice: bool = False
    authorize_clinician: bool = False
    candidate_search: CandidateSearch | None = None
    candidate_search_depth: int = 500
    fixed_candidate_ranking: tuple[TrialSearchRank, ...] | None = None
    run_mode: str | None = None
    session_metadata: Mapping[str, object] | None = None

    def __post_init__(self) -> None:
        if self.candidate_search_depth < 1:
            raise ValueError("candidate_search_depth must be at least one")
        resume_only = (
            self.retry_unavailable
            or self.approve_patient_choice
            or self.authorize_clinician
        )
        if resume_only and self.resume_path is None:
            raise ValueError("retry and approval options require resume_path")


@dataclass(frozen=True, slots=True)
class GeneralRunOutcome:
    result_path: Path | None
    trace_path: Path
    session_path: Path
    paused: bool


def _mark_synthetic_answer(item: HiddenFactAnswer) -> HiddenFactAnswer:
    if item.evidence.input_provenance is not None:
        return item
    provenance = EvidenceInputProvenance(
        capture_method=EvidenceCaptureMethod.SYNTHETIC_ENVIRONMENT,
        requested_action=item.access_path,
        source_type_declared=True,
        source_location_declared=True,
        verification_status_declared=True,
        event_date_declared=item.evidence.event_date is not None,
        recorded_date_declared=item.evidence.recorded_date is not None,
    )
    evidence = item.evidence.model_copy(
        update={"input_provenance": provenance}
    )
    return item.model_copy(update={"evidence": evidence})


def _read_answers(path: Path) -> list[HiddenFactAnswer]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        raw = raw.get("answers")
    if not isinstance(raw, list):
        raise ValueError("answer JSON must be a list or an object with answers")
    return [
        _mark_synthetic_answer(HiddenFactAnswer.model_validate(item)) for item in raw
    ]


def _agents(model: StructuredModel) -> EpisodeAgents:
    return EpisodeAgents(
        coordinator=CoordinatorAgent(model),
        matcher_judge=MatcherJudgeAgent(model),
        next_evidence=NextEvidenceAgent(model),
        selective_reviewer=SelectiveReviewerAgent(model),
    )


def _show_final(
    result: dict,
    write: Callable[[str], None],
    *,
    model_label: str,
) -> None:
    titles = {
        str(item["source"]["trial_id"]): str(item["source"]["title"])
        for item in result["input"].get("candidate_hits", [])
    }
    write("")
    for line in build_terminal_summary_lines(
        result,
        titles=titles,
        model_label=model_label,
    ):
        write(line)


def _candidate_metadata(prepared: PreparedGeneralCase) -> dict[str, object]:
    return {
        "candidate_trial_ids": [
            item.source.trial_id for item in prepared.candidate_hits
        ],
        "candidate_search_method": prepared.candidate_hits[0].retrieval_method,
        "candidate_ranking": [
            item.model_dump(mode="json")
            for item in prepared.case.candidate_ranking
        ],
    }


def _prepare_session(
    *,
    options: GeneralRunOptions,
    patient: GeneralPatientInput,
    sources: list[StructuredTrialSource],
    model_label: str,
    session_path: Path,
) -> tuple[SessionStore, PreparedGeneralCase]:
    if options.resume_path is None:
        prepared = prepare_general_case(
            patient,
            sources,
            candidate_search=options.candidate_search,
            search_depth=options.candidate_search_depth,
            fixed_candidate_ranking=(
                None
                if options.fixed_candidate_ranking is None
                else list(options.fixed_candidate_ranking)
            ),
        )
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
                    **dict(options.session_metadata or {}),
                    **_candidate_metadata(prepared),
                },
            ),
        )
        store.save()
        return store, prepared

    store = SessionStore.load(options.resume_path)
    if store.session.case_id != patient.case_id:
        raise ValueError("resume session belongs to a different case_id")
    if options.retry_unavailable:
        store.clear_unavailable_facts()
    if options.approve_patient_choice or options.authorize_clinician:
        store.approve_pending_option(
            patient_choice=options.approve_patient_choice,
            clinician_authorization=options.authorize_clinician,
        )
    saved_candidate_ranking = store.session.metadata.get("candidate_ranking")
    if isinstance(saved_candidate_ranking, list):
        try:
            ranking = [
                TrialSearchRank.model_validate(item)
                for item in saved_candidate_ranking
            ]
        except (TypeError, ValueError):
            ranking = []
    else:
        ranking = []
    saved_candidate_ids = store.session.metadata.get("candidate_trial_ids")
    if ranking:
        prepared = prepare_general_case(
            patient,
            sources,
            fixed_candidate_ranking=ranking,
        )
    elif isinstance(saved_candidate_ids, list) and all(
        isinstance(item, str) for item in saved_candidate_ids
    ):
        prepared = prepare_general_case(
            patient,
            sources,
            fixed_candidate_trial_ids=saved_candidate_ids,
            fixed_retrieval_method=str(
                store.session.metadata.get(
                    "candidate_search_method", "saved-session-candidates"
                )
            ),
        )
    else:
        prepared = prepare_general_case(
            patient,
            sources,
            candidate_search=options.candidate_search,
            search_depth=options.candidate_search_depth,
        )
        metadata = {**store.session.metadata, **_candidate_metadata(prepared)}
        store.session = store.session.model_copy(update={"metadata": metadata})
        store.save()
    return store, prepared


def _save_finished_session(
    *,
    store: SessionStore,
    screening: PatientScreeningResult,
    previous_action_count: int,
) -> None:
    revealed = list(store.session.revealed_fact_ids)
    unavailable = list(store.session.unavailable_fact_ids)
    for item in screening.action_history:
        fact_id = item.agent_action.target_fact_id
        if (
            item.tool_result.status is EnvironmentStatus.REVEALED
            and fact_id is not None
        ):
            if fact_id not in revealed:
                revealed.append(fact_id)
            unavailable = [value for value in unavailable if value != fact_id]
        elif (
            item.tool_result.status is EnvironmentStatus.NOT_AVAILABLE
            and fact_id is not None
        ):
            if fact_id not in unavailable:
                unavailable.append(fact_id)

    resumable_stop_reasons = {
        PatientScreeningStopReason.AWAITING_PATIENT_CHOICE,
        PatientScreeningStopReason.AWAITING_CLINICIAN_AUTHORIZATION,
        PatientScreeningStopReason.DEFERRED,
        PatientScreeningStopReason.TOOL_RETURNED_NO_INFORMATION,
    }
    pending_option_id = None
    if screening.stop_reason in {
        PatientScreeningStopReason.AWAITING_PATIENT_CHOICE,
        PatientScreeningStopReason.AWAITING_CLINICIAN_AUTHORIZATION,
    } and screening.guidance.selected_option is not None:
        pending_option_id = screening.guidance.selected_option.option_id
    store.session = store.session.model_copy(
        update={
            "patient_state": screening.final_patient_state,
            "revealed_fact_ids": revealed,
            "unavailable_fact_ids": unavailable,
            "pending_option_id": pending_option_id,
            "action_count": max(
                store.session.action_count,
                previous_action_count + len(screening.action_history),
            ),
            "completed": screening.stop_reason not in resumable_stop_reasons,
            "result": screening,
        }
    )
    store.save()


def run_general_screening(
    *,
    options: GeneralRunOptions,
    model: StructuredModel,
    model_label: str,
    medical_disclaimer: str,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
    trace: TraceRecorder | None = None,
) -> GeneralRunOutcome:
    patient = load_general_patient(options.patient_path)
    sources = load_structured_trials(options.trials_path)
    options.output_dir.mkdir(parents=True, exist_ok=True)
    trace_path = options.output_dir / "trace.jsonl"
    result_path = options.output_dir / "result.json"
    session_path = options.resume_path or options.output_dir / "session.json"
    store, prepared = _prepare_session(
        options=options,
        patient=patient,
        sources=sources,
        model_label=model_label,
        session_path=session_path,
    )

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
    recorder = trace or TraceRecorder(case.case_id)
    recorder.record(
        cycle=0,
        actor="candidate_trial_search",
        event="candidate_trials_selected",
        input_refs=patient.search_conditions,
        output={
            "trial_pool_count": prepared.trial_pool_count,
            "trial_ids": [item.source.trial_id for item in prepared.candidate_hits],
            "scores": [item.score for item in prepared.candidate_hits],
            "method": prepared.candidate_hits[0].retrieval_method,
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
            initial_unavailable_fact_ids=set(store.session.unavailable_fact_ids),
            patient_approved_option_ids=set(
                store.session.patient_approved_option_ids
            ),
            clinician_authorized_option_ids=set(
                store.session.clinician_authorized_option_ids
            ),
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
        "run_mode": options.run_mode or (
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
            **dict(options.session_metadata or {}),
        },
        "screening": screening.model_dump(mode="json"),
        "usage": usage.model_dump(mode="json"),
        "medical_disclaimer": medical_disclaimer,
    }
    atomic_write_text(
        result_path,
        json.dumps(result_document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    recorder.write_jsonl(trace_path)
    _save_finished_session(
        store=store,
        screening=screening,
        previous_action_count=previous_action_count,
    )
    _show_final(result_document, write, model_label=model_label)
    write(f"결과 파일: {result_path}")
    write(medical_disclaimer)
    return GeneralRunOutcome(
        result_path=result_path,
        trace_path=trace_path,
        session_path=session_path,
        paused=False,
    )


__all__ = ["GeneralRunOptions", "GeneralRunOutcome", "run_general_screening"]
