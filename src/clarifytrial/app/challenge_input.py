"""Validate topic files and materialize the shared structured inputs."""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path

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
from ..preparation import (
    NaturalScreeningRequest,
    PreparedScreeningCase,
    RawPatientRecord,
)
from .challenge_contracts import ChallengeTopic, ChallengeTopicsInput
from .contracts import GeneralPatientInput, StructuredTrialSource


def load_challenge_topics(path: str | Path) -> ChallengeTopicsInput:
    """Read and validate the team's ``topics[num, title]`` JSON file."""

    source = Path(path)
    return ChallengeTopicsInput.model_validate_json(
        source.read_text(encoding="utf-8")
    )


def select_challenge_topics(
    document: ChallengeTopicsInput,
    *,
    topic_ids: tuple[str, ...],
    all_topics: bool,
) -> list[ChallengeTopic]:
    """Select topics while preserving the order declared by the caller."""

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
    cache_summary: Mapping[str, object] | None = None,
) -> tuple[Path, Path]:
    """Save adapter results in the files accepted by ``run-screening``."""

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
                item.model_dump(mode="json") for item in case.candidate_ranking
            ],
            "missing_information": [
                item.model_dump(mode="json") for item in case.evidence_requests
            ],
            "trial_protocol_cache": dict(cache_summary or {}),
        },
    )
    return patient_path, trials_path


__all__ = [
    "add_direct_input_options",
    "challenge_topic_request",
    "load_challenge_topics",
    "materialize_prepared_topic",
    "select_challenge_topics",
]
