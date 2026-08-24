"""Orchestrate topic preparation and the shared screening workflow."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ..agents import (
    CoordinatorAgent,
    MatcherJudgeAgent,
    NextEvidenceAgent,
    SelectiveReviewerAgent,
)
from ..llm import StructuredModel
from ..preparation import (
    CandidateSearch,
    NaturalScreeningPipeline,
    TrialProtocolCache,
)
from ..preparation.patient_record import PatientRecordStructurerAgent
from ..preparation.trial_protocol import TrialProtocolStructurerAgent
from ..trace import TraceRecorder
from ..workflow import EpisodeAgents, PatientScreeningRunner
from .challenge_contracts import (
    ChallengeRunOptions,
    ChallengeRunOutcome,
    ChallengeTopic,
)
from .challenge_input import (
    add_direct_input_options,
    challenge_topic_request,
    load_challenge_topics,
    materialize_prepared_topic,
    select_challenge_topics,
)
from .contracts import ScreeningSession
from .runner import GeneralRunOptions, GeneralRunOutcome, run_general_screening


def _episode_agents(model: StructuredModel) -> EpisodeAgents:
    return EpisodeAgents(
        coordinator=CoordinatorAgent(model),
        matcher_judge=MatcherJudgeAgent(model),
        next_evidence=NextEvidenceAgent(model),
        selective_reviewer=SelectiveReviewerAgent(model),
    )


def _run_resumed_topic(
    *,
    options: ChallengeRunOptions,
    topic: ChallengeTopic,
    model: StructuredModel,
    model_label: str,
    medical_disclaimer: str,
    read: Callable[[str], str],
    write: Callable[[str], None],
) -> GeneralRunOutcome:
    assert options.resume_path is not None
    session = ScreeningSession.model_validate_json(
        options.resume_path.read_text(encoding="utf-8")
    )
    if session.case_id != topic.num:
        raise ValueError("resume session belongs to a different topic")
    patient_path = Path(str(session.metadata.get("patient_path", "")))
    trials_path = Path(str(session.metadata.get("trials_path", "")))
    if not patient_path.is_file() or not trials_path.is_file():
        raise ValueError("resume session cannot find its prepared input files")
    return run_general_screening(
        options=GeneralRunOptions(
            patient_path=patient_path,
            trials_path=trials_path,
            output_dir=options.resume_path.parent,
            settings=options.settings,
            resume_path=options.resume_path,
            retry_unavailable=options.retry_unavailable,
            approve_patient_choice=options.approve_patient_choice,
            authorize_clinician=options.authorize_clinician,
            run_mode="challenge_topic_interactive",
            session_metadata={"challenge_topic_id": topic.num},
        ),
        model=model,
        model_label=model_label,
        medical_disclaimer=medical_disclaimer,
        read=read,
        write=write,
    )


def _run_new_topic(
    *,
    options: ChallengeRunOptions,
    topic: ChallengeTopic,
    topic_count: int,
    model: StructuredModel,
    model_label: str,
    candidate_search: CandidateSearch,
    medical_disclaimer: str,
    read: Callable[[str], str],
    write: Callable[[str], None],
) -> GeneralRunOutcome:
    topic_dir = (
        options.output_dir / topic.num
        if options.all_topics or topic_count > 1
        else options.output_dir
    )
    topic_dir.mkdir(parents=True, exist_ok=True)
    trace = TraceRecorder(topic.num)
    request = challenge_topic_request(
        topic,
        source_path=options.topics_path,
        as_of=options.as_of,
        candidate_count=options.candidate_count,
    )
    trial_cache = TrialProtocolCache(
        options.trial_protocol_cache_dir,
        model_label=model_label,
    )
    pipeline = NaturalScreeningPipeline(
        patient_structurer=PatientRecordStructurerAgent(model),
        trial_structurer=TrialProtocolStructurerAgent(model),
        candidate_search=candidate_search,
        screening_runner=PatientScreeningRunner(
            _episode_agents(model),
            options.settings,
        ),
        trial_protocol_cache=trial_cache,
    )
    write("")
    write(f"입력 {topic.num} 준비")
    prepared = add_direct_input_options(pipeline.prepare(request, trace=trace))
    write("찾은 질환·상태: " + " / ".join(prepared.search_conditions))
    write(f"환자 기록에서 확인한 사실: {len(prepared.patient_state.facts)}개")
    write(f"검토할 임상시험: {len(prepared.candidate_hits)}개")
    cache_summary = trial_cache.stats.model_dump(mode="json")
    write(
        "시험 조건 정리: "
        f"저장본 재사용 {cache_summary['reused_trial_count']}개, "
        f"새로 정리 {cache_summary['newly_structured_trial_count']}개"
    )
    patient_path, trials_path = materialize_prepared_topic(
        topic=topic,
        prepared=prepared,
        output_dir=topic_dir,
        cache_summary=cache_summary,
    )
    return run_general_screening(
        options=GeneralRunOptions(
            patient_path=patient_path,
            trials_path=trials_path,
            output_dir=topic_dir,
            settings=options.settings,
            fixed_candidate_ranking=tuple(prepared.screening_case.candidate_ranking),
            run_mode="challenge_topic_interactive",
            session_metadata={
                "challenge_topics_path": str(options.topics_path),
                "challenge_topic_id": topic.num,
                "trial_protocol_cache_dir": str(options.trial_protocol_cache_dir),
                "trial_protocol_cache": cache_summary,
            },
        ),
        model=model,
        model_label=model_label,
        medical_disclaimer=medical_disclaimer,
        read=read,
        write=write,
        trace=trace,
    )


def run_challenge_screening(
    *,
    options: ChallengeRunOptions,
    model: StructuredModel,
    model_label: str,
    candidate_search: CandidateSearch | None,
    medical_disclaimer: str,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> ChallengeRunOutcome:
    """Run selected competition topics from free text to final ranked lists."""

    document = load_challenge_topics(options.topics_path)
    topics = select_challenge_topics(
        document,
        topic_ids=options.topic_ids,
        all_topics=options.all_topics,
    )
    outcomes: list[GeneralRunOutcome] = []
    for topic in topics:
        if options.resume_path is not None:
            outcome = _run_resumed_topic(
                options=options,
                topic=topic,
                model=model,
                model_label=model_label,
                medical_disclaimer=medical_disclaimer,
                read=read,
                write=write,
            )
        else:
            if candidate_search is None:
                raise ValueError("a candidate search is required for a new topic")
            outcome = _run_new_topic(
                options=options,
                topic=topic,
                topic_count=len(topics),
                model=model,
                model_label=model_label,
                candidate_search=candidate_search,
                medical_disclaimer=medical_disclaimer,
                read=read,
                write=write,
            )
        outcomes.append(outcome)
        if options.resume_path is not None or outcome.paused:
            break
    return ChallengeRunOutcome(
        topic_ids=tuple(topic.num for topic in topics[: len(outcomes)]),
        runs=tuple(outcomes),
    )


__all__ = ["run_challenge_screening"]
