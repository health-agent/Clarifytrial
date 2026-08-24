"""Connect competition-style ``topics`` JSON to the complete workflow."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import Field, model_validator

from ..agents import (
    CoordinatorAgent,
    MatcherJudgeAgent,
    NextEvidenceAgent,
    SelectiveReviewerAgent,
)
from ..contracts import (
    ContractModel,
    EvidenceSourceType,
    NextAction,
    VerificationStatus,
)
from ..interactive.burden_contracts import (
    AcquisitionMode,
    AcquisitionOption,
    DirectCostBand,
)
from ..llm import StructuredModel
from ..preparation import (
    CandidateSearch,
    NaturalScreeningPipeline,
    NaturalScreeningRequest,
    PreparedScreeningCase,
    RawPatientRecord,
)
from ..preparation.patient_record import PatientRecordStructurerAgent
from ..preparation.trial_protocol import TrialProtocolStructurerAgent
from ..settings import EpisodeSettings
from ..trace import TraceRecorder
from ..workflow import EpisodeAgents, PatientScreeningRunner
from .contracts import GeneralPatientInput, ScreeningSession, StructuredTrialSource
from .runner import GeneralRunOptions, GeneralRunOutcome, run_general_screening


class ChallengeTopic(ContractModel):
    """One supplied synthetic patient vignette."""

    num: str = Field(min_length=1)
    title: str = Field(min_length=1)


class ChallengeTopicsInput(ContractModel):
    """Competition transport format supplied by the team."""

    topics: list[ChallengeTopic] = Field(min_length=1)

    @model_validator(mode="after")
    def topic_numbers_are_unique(self) -> "ChallengeTopicsInput":
        values = [item.num for item in self.topics]
        if len(values) != len(set(values)):
            raise ValueError("topics must not repeat num")
        return self


@dataclass(frozen=True, slots=True)
class ChallengeRunOptions:
    topics_path: Path
    output_dir: Path
    topic_ids: tuple[str, ...]
    all_topics: bool
    as_of: datetime
    candidate_count: int
    settings: EpisodeSettings
    resume_path: Path | None = None
    retry_unavailable: bool = False
    approve_patient_choice: bool = False
    authorize_clinician: bool = False

    def __post_init__(self) -> None:
        if self.candidate_count < 1:
            raise ValueError("candidate_count must be at least one")
        if self.all_topics == bool(self.topic_ids):
            raise ValueError("choose topic_ids or all_topics")
        if self.all_topics and self.resume_path is not None:
            raise ValueError("resume supports one topic at a time")


@dataclass(frozen=True, slots=True)
class ChallengeRunOutcome:
    topic_ids: tuple[str, ...]
    runs: tuple[GeneralRunOutcome, ...]


def load_challenge_topics(path: str | Path) -> ChallengeTopicsInput:
    """Read and validate the team's ``topics[num, title]`` JSON file."""

    source = Path(path)
    return ChallengeTopicsInput.model_validate_json(
        source.read_text(encoding="utf-8")
    )


def _selected_topics(
    document: ChallengeTopicsInput,
    *,
    topic_ids: tuple[str, ...],
    all_topics: bool,
) -> list[ChallengeTopic]:
    if all_topics:
        return list(document.topics)
    by_id = {item.num: item for item in document.topics}
    missing = [item for item in topic_ids if item not in by_id]
    if missing:
        raise ValueError("topic num not found: " + ", ".join(missing))
    if len(topic_ids) != len(set(topic_ids)):
        raise ValueError("topic_ids must not contain duplicates")
    return [by_id[item] for item in topic_ids]


def challenge_topic_request(
    topic: ChallengeTopic,
    *,
    source_path: Path,
    as_of: datetime,
    candidate_count: int,
) -> NaturalScreeningRequest:
    """Turn one topic into the existing cited natural-record request."""

    return NaturalScreeningRequest(
        case_id=topic.num,
        patient_record=RawPatientRecord(
            patient_id=topic.num,
            source_id=(
                f"{source_path.resolve()}#topics[num={topic.num}].title"
            ),
            text=topic.title,
            recorded_at=as_of,
            as_of=as_of,
            source_type=EvidenceSourceType.SYNTHETIC_CASE,
            verification_status=VerificationStatus.VERIFIED,
        ),
        candidate_count=candidate_count,
    )


_DEFAULT_PATHS = (
    (
        NextAction.ASK_PATIENT,
        "ask-patient",
        AcquisitionMode.PATIENT_REPORT,
    ),
    (
        NextAction.LOOKUP_RECORD,
        "lookup-existing-record",
        AcquisitionMode.OUTSIDE_RECORD,
    ),
    (
        NextAction.REQUEST_VERIFICATION,
        "provide-existing-result",
        AcquisitionMode.EXISTING_OFFICIAL_RESULT,
    ),
)


def add_direct_input_options(
    prepared: PreparedScreeningCase,
) -> PreparedScreeningCase:
    """Offer typed or file answers without pretending that a new test exists."""

    case = prepared.screening_case
    if case.acquisition_options:
        return prepared
    options: list[AcquisitionOption] = []
    for request in case.evidence_requests:
        for action, path_key, mode in _DEFAULT_PATHS:
            if action not in request.acceptable_actions:
                continue
            direct_patient_answer = action is NextAction.ASK_PATIENT
            options.append(
                AcquisitionOption(
                    option_id=f"{request.fact_id}:{path_key}",
                    fact_id=request.fact_id,
                    action=action,
                    acquisition_mode=mode,
                    available_now=True,
                    expected_delay_hours=0 if direct_patient_answer else None,
                    visit_required=False if direct_patient_answer else None,
                    direct_cost_band=(
                        DirectCostBand.NONE
                        if direct_patient_answer
                        else DirectCostBand.UNKNOWN
                    ),
                    physical_burden_0_to_3=0 if direct_patient_answer else None,
                    emotional_burden_0_to_3=0 if direct_patient_answer else None,
                    medical_risk_0_to_3=0 if direct_patient_answer else None,
                    treatment_disruption_0_to_3=0,
                    new_test_required=False,
                    requires_patient_choice=False,
                    requires_clinician_authorization=False,
                    source_note=(
                        "이번 실행에서 답변 문장이나 기존 자료 JSON 파일을 "
                        "직접 제공하는 입력 경로"
                    ),
                )
            )
    return prepared.model_copy(
        update={
            "screening_case": case.model_copy(
                update={"acquisition_options": options}
            )
        }
    )


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(value, ContractModel):
        payload = value.model_dump(mode="json")
    else:
        payload = value
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def materialize_prepared_topic(
    *,
    topic: ChallengeTopic,
    prepared: PreparedScreeningCase,
    output_dir: Path,
) -> tuple[Path, Path]:
    """Save the adapter result in the same files accepted by ``run-screening``."""

    case = prepared.screening_case
    patient = GeneralPatientInput(
        case_id=case.case_id,
        search_conditions=prepared.search_conditions,
        candidate_count=len(prepared.candidate_hits),
        patient_state=case.initial_patient_state,
        evidence_requests=case.evidence_requests,
        acquisition_options=case.acquisition_options,
        patient_burden_input=case.patient_burden_input,
    )
    trial_by_id = {item.trial_id: item for item in case.trials}
    trials = [
        StructuredTrialSource(
            trial_id=hit.source.trial_id,
            title=hit.source.title,
            conditions=hit.source.conditions,
            summary=hit.source.summary,
            source_location=hit.source.source_location,
            trial=trial_by_id[hit.source.trial_id],
        )
        for hit in prepared.candidate_hits
    ]
    patient_path = output_dir / "prepared-patient.json"
    trials_path = output_dir / "prepared-trials.json"
    _write_json(patient_path, patient)
    _write_json(trials_path, [item.model_dump(mode="json") for item in trials])
    _write_json(
        output_dir / "prepared-input.json",
        {
            "original_topic": topic.model_dump(mode="json"),
            "search_conditions": prepared.search_conditions,
            "patient_facts": [
                item.model_dump(mode="json") for item in prepared.patient_state.facts
            ],
            "candidate_ranking": [
                item.model_dump(mode="json")
                for item in case.candidate_ranking
            ],
            "missing_information": [
                item.model_dump(mode="json") for item in case.evidence_requests
            ],
        },
    )
    return patient_path, trials_path


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
    topics = _selected_topics(
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
            outcomes.append(outcome)
            break

        topic_dir = (
            options.output_dir / topic.num
            if options.all_topics or len(topics) > 1
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
        if candidate_search is None:
            raise ValueError("a candidate search is required for a new topic")
        pipeline = NaturalScreeningPipeline(
            patient_structurer=PatientRecordStructurerAgent(model),
            trial_structurer=TrialProtocolStructurerAgent(model),
            candidate_search=candidate_search,
            screening_runner=PatientScreeningRunner(
                _episode_agents(model),
                options.settings,
            ),
        )
        write("")
        write(f"입력 {topic.num} 준비")
        prepared = add_direct_input_options(
            pipeline.prepare(request, trace=trace)
        )
        write(
            "찾은 질환·상태: " + " / ".join(prepared.search_conditions)
        )
        write(f"환자 기록에서 확인한 사실: {len(prepared.patient_state.facts)}개")
        write(f"검토할 임상시험: {len(prepared.candidate_hits)}개")
        patient_path, trials_path = materialize_prepared_topic(
            topic=topic,
            prepared=prepared,
            output_dir=topic_dir,
        )
        outcome = run_general_screening(
            options=GeneralRunOptions(
                patient_path=patient_path,
                trials_path=trials_path,
                output_dir=topic_dir,
                settings=options.settings,
                fixed_candidate_ranking=tuple(
                    prepared.screening_case.candidate_ranking
                ),
                run_mode="challenge_topic_interactive",
                session_metadata={
                    "challenge_topics_path": str(options.topics_path),
                    "challenge_topic_id": topic.num,
                },
            ),
            model=model,
            model_label=model_label,
            medical_disclaimer=medical_disclaimer,
            read=read,
            write=write,
            trace=trace,
        )
        outcomes.append(outcome)
        if outcome.paused:
            break
    return ChallengeRunOutcome(
        topic_ids=tuple(topic.num for topic in topics[: len(outcomes)]),
        runs=tuple(outcomes),
    )


__all__ = [
    "ChallengeRunOptions",
    "ChallengeRunOutcome",
    "ChallengeTopic",
    "ChallengeTopicsInput",
    "add_direct_input_options",
    "challenge_topic_request",
    "load_challenge_topics",
    "materialize_prepared_topic",
    "run_challenge_screening",
]
