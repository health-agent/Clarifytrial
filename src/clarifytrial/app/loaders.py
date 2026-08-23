"""Load generic patient and structured-trial JSON without fixed disease names."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..preparation import InMemoryCandidateSearch, TrialProtocolSource
from ..preparation.contracts import CandidateSearchHit
from ..workflow import PatientScreeningCase
from .contracts import GeneralPatientInput, StructuredTrialSource


@dataclass(frozen=True, slots=True)
class PreparedGeneralCase:
    case: PatientScreeningCase
    candidate_hits: tuple[CandidateSearchHit, ...]
    trial_pool_count: int


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_general_patient(path: str | Path) -> GeneralPatientInput:
    return GeneralPatientInput.model_validate(_read_json(Path(path)))


def load_structured_trials(path: str | Path) -> list[StructuredTrialSource]:
    source = Path(path)
    text = source.read_text(encoding="utf-8")
    stripped = text.lstrip()
    if stripped.startswith("["):
        raw = json.loads(text)
        if not isinstance(raw, list):
            raise ValueError("trial JSON array is invalid")
        rows = raw
    elif stripped.startswith("{"):
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as error:
            if "Extra data" not in str(error):
                raise
            rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        else:
            if not isinstance(raw, dict) or not isinstance(raw.get("trials"), list):
                raise ValueError("trial JSON object needs a trials list")
            rows = raw["trials"]
    else:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    trials = [StructuredTrialSource.model_validate(item) for item in rows]
    ids = [item.trial_id for item in trials]
    if len(ids) != len(set(ids)):
        raise ValueError("trial sources must not repeat trial_id")
    if not trials:
        raise ValueError("trial source file is empty")
    return trials


def prepare_general_case(
    patient: GeneralPatientInput,
    trial_sources: list[StructuredTrialSource],
) -> PreparedGeneralCase:
    source_by_id = {item.trial_id: item for item in trial_sources}
    search_sources = [
        TrialProtocolSource(
            trial_id=item.trial_id,
            title=item.title,
            conditions=item.conditions,
            summary=item.summary,
            eligibility_text="\n".join(
                criterion.statement for criterion in item.trial.criteria
            ),
            source_location=item.source_location,
        )
        for item in trial_sources
    ]
    hits = InMemoryCandidateSearch(search_sources).search(
        patient.search_conditions,
        top_k=min(patient.candidate_count, len(search_sources)),
    )
    if not hits:
        raise ValueError("candidate search returned no trial with a positive match")
    selected_ids = [item.source.trial_id for item in hits]
    selected_trials = [source_by_id[item].trial for item in selected_ids]
    selected_criterion_ids = {
        criterion.criterion_id
        for trial in selected_trials
        for criterion in trial.criteria
    }
    requests = []
    for request in patient.evidence_requests:
        related = [
            item
            for item in request.related_criterion_ids
            if item in selected_criterion_ids
        ]
        if related:
            requests.append(request.model_copy(update={"related_criterion_ids": related}))
    selected_fact_ids = {item.fact_id for item in requests}
    options = [
        item
        for item in patient.acquisition_options
        if item.fact_id in selected_fact_ids
    ]
    case = PatientScreeningCase(
        case_id=patient.case_id,
        disease_group=" / ".join(patient.search_conditions),
        trials=selected_trials,
        initial_patient_state=patient.patient_state,
        evidence_requests=requests,
        acquisition_options=options,
        patient_burden_input=patient.patient_burden_input,
    )
    return PreparedGeneralCase(
        case=case,
        candidate_hits=tuple(hits),
        trial_pool_count=len(trial_sources),
    )


__all__ = [
    "PreparedGeneralCase",
    "load_general_patient",
    "load_structured_trials",
    "prepare_general_case",
]
