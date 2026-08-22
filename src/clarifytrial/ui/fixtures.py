"""Build a public, synthetic case for the integrated terminal interface."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..contracts import (
    EvidenceFact,
    EvidenceSourceType,
    NextAction,
    NextEvidenceRequest,
    NumericConstraint,
    PatientState,
    TrialCriterion,
)
from ..environment import HiddenFactAnswer
from ..interactive.burden_contracts import AcquisitionMode, AcquisitionOption
from ..preparation import InMemoryCandidateSearch
from ..preparation.contracts import CandidateSearchHit, TrialProtocolSource
from ..workflow import PatientScreeningCase, ScreeningTrial


_GROUP_CONDITIONS = {
    "type_2_diabetes": "type 2 diabetes",
    "breast_cancer": "breast cancer",
    "major_depressive_disorder": "major depressive disorder",
}

_ACTION_MODES = {
    NextAction.ASK_PATIENT: AcquisitionMode.PATIENT_REPORT,
    NextAction.LOOKUP_RECORD: AcquisitionMode.INTERNAL_RECORD,
    NextAction.REQUEST_VERIFICATION: AcquisitionMode.EXISTING_OFFICIAL_RESULT,
}

_SOURCE_ACTIONS = {
    EvidenceSourceType.PATIENT_REPORT: NextAction.ASK_PATIENT,
    EvidenceSourceType.MEDICAL_RECORD: NextAction.LOOKUP_RECORD,
    EvidenceSourceType.OFFICIAL_VERIFICATION: NextAction.REQUEST_VERIFICATION,
}


@dataclass(frozen=True, slots=True)
class IntegratedUIFixture:
    """Visible structured input and private synthetic answers for one run."""

    screening_case: PatientScreeningCase
    trial_sources: tuple[TrialProtocolSource, ...]
    candidate_hits: tuple[CandidateSearchHit, ...]
    hidden_answers: tuple[HiddenFactAnswer, ...]
    search_conditions: tuple[str, ...]
    expected_candidate_trial_ids: tuple[str, ...]
    trial_set_path: str
    patient_pairs_path: str
    generation_config_path: str


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _trial_sources(document: dict[str, Any]) -> tuple[TrialProtocolSource, ...]:
    criteria_by_trial: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in document["criteria"]:
        criteria_by_trial[str(row["nct_id"])].append(row)

    sources = []
    for trial in document["trials"]:
        trial_id = str(trial["nct_id"])
        group_id = str(trial["group_id"])
        rows = sorted(
            criteria_by_trial[trial_id],
            key=lambda item: (int(item["line_number"]), str(item["criterion_id"])),
        )
        eligibility_text = "\n".join(str(item["source_text"]) for item in rows)
        sources.append(
            TrialProtocolSource(
                trial_id=trial_id,
                title=str(trial["title"]),
                conditions=[
                    _GROUP_CONDITIONS[group_id],
                    str(trial["group_label"]),
                ],
                summary=str(trial["title"]),
                eligibility_text=eligibility_text,
                source_location=f"{trial['study_url']}#eligibility",
            )
        )
    return tuple(sources)


def _chosen_action(
    raw_actions: list[str],
    evidence_source: EvidenceSourceType,
) -> NextAction:
    actions = {NextAction(item) for item in raw_actions}
    source_action = _SOURCE_ACTIONS.get(evidence_source)
    if source_action in actions:
        return source_action
    for action in (
        NextAction.ASK_PATIENT,
        NextAction.LOOKUP_RECORD,
        NextAction.REQUEST_VERIFICATION,
    ):
        if action in actions:
            return action
    raise ValueError("missing information has no supported acquisition action")


def _screening_trials(
    *,
    trial_document: dict[str, Any],
    group_id: str,
    candidate_ids: list[str],
) -> list[ScreeningTrial]:
    metadata = {
        str(item["nct_id"]): item for item in trial_document["trials"]
    }
    criteria_by_trial: dict[str, list[TrialCriterion]] = defaultdict(list)
    for row in trial_document["criteria"]:
        trial_id = str(row["nct_id"])
        if trial_id not in candidate_ids:
            continue
        numeric = None
        if row["operator"] is not None:
            numeric = NumericConstraint(
                concept=f"{group_id}:{row['fact_code']}",
                operator=str(row["operator"]),
                threshold=float(row["threshold"]),
                unit=str(row["unit"]),
            )
        trial = metadata[trial_id]
        criteria_by_trial[trial_id].append(
            TrialCriterion(
                criterion_id=str(row["criterion_id"]),
                trial_id=trial_id,
                kind=str(row["kind"]),
                statement=str(row["source_text"]),
                source_location=(
                    f"{trial['study_url']}#eligibility-line={row['line_number']}"
                ),
                required=True,
                numeric_constraint=numeric,
            )
        )
    return [
        ScreeningTrial(trial_id=trial_id, criteria=criteria_by_trial[trial_id])
        for trial_id in candidate_ids
    ]


def _acquisition_option(
    *,
    request: NextEvidenceRequest,
    action: NextAction,
) -> AcquisitionOption:
    mode = _ACTION_MODES[action]
    return AcquisitionOption(
        option_id=f"{request.fact_id}:integrated-ui-{mode.value}",
        fact_id=request.fact_id,
        action=action,
        acquisition_mode=mode,
        available_now=True,
        expected_delay_hours=0,
        visit_required=False,
        direct_cost_band="none",
        physical_burden_0_to_3=0,
        emotional_burden_0_to_3=0,
        medical_risk_0_to_3=0,
        treatment_disruption_0_to_3=0,
        source_note="합성 평가자료에 미리 정해 둔 답을 같은 경로로 확인",
    )


def build_integrated_ui_fixture(
    *,
    trial_set_path: str | Path,
    patient_pairs_path: str | Path,
    generation_config_path: str | Path,
    patient_id: str,
) -> IntegratedUIFixture:
    """Create one structured full-path case with action-gated synthetic answers."""

    trial_set_path = Path(trial_set_path)
    patient_pairs_path = Path(patient_pairs_path)
    generation_config_path = Path(generation_config_path)
    trial_document = _read_json(trial_set_path)
    pairs_document = _read_json(patient_pairs_path)
    generation_config = _read_json(generation_config_path)
    pair = next(
        (
            item
            for item in pairs_document["pairs"]
            if str(item["patient_id"]) == patient_id
        ),
        None,
    )
    if pair is None:
        raise ValueError(f"unknown patient ID: {patient_id}")

    group_id = str(pair["group_id"])
    condition = _GROUP_CONDITIONS[group_id]
    all_sources = _trial_sources(trial_document)
    group_sources = tuple(
        source
        for source in all_sources
        if condition.casefold()
        in {item.casefold() for item in source.conditions}
    )
    candidate_hits = tuple(
        InMemoryCandidateSearch(group_sources).search(
            [condition],
            top_k=len(pair["trial_ids"]),
        )
    )
    candidate_ids = [item.source.trial_id for item in candidate_hits]
    expected_ids = [str(item) for item in pair["trial_ids"]]
    if set(candidate_ids) != set(expected_ids):
        raise ValueError("condition-filtered search did not recover the declared trials")

    pivotal = set(str(item) for item in pair["pivotal_fact_codes"])
    initial_facts = []
    for raw_fact in pair["insufficient_evidence_episode"]["evidence"]:
        fact = EvidenceFact.model_validate(raw_fact)
        fact_code = "" if fact.concept is None else fact.concept.rsplit(":", 1)[-1]
        if fact_code not in pivotal:
            initial_facts.append(fact)
    patient_state = PatientState(
        patient_id=patient_id,
        as_of=str(generation_config["as_of"]),
        facts=initial_facts,
    )

    requests = [
        NextEvidenceRequest.model_validate(item)
        for item in pair["insufficient_evidence_episode"]["missing_information"]
    ]
    answer_by_key: dict[str, EvidenceFact] = {}
    for raw_answer in pair["insufficient_evidence_episode"]["verification_answers"]:
        answer = EvidenceFact.model_validate(raw_answer)
        if answer.concept is None:
            raise ValueError("synthetic verification answer needs a concept")
        answer_by_key[answer.concept.rsplit(":", 1)[-1]] = answer

    options = []
    hidden_answers = []
    for request in requests:
        fact_key = request.fact_id.rsplit(":", 1)[-1]
        answer = answer_by_key.get(fact_key)
        if answer is None:
            raise ValueError(f"missing synthetic answer for {fact_key}")
        action = _chosen_action(
            [item.value for item in request.acceptable_actions],
            answer.source_type,
        )
        options.append(_acquisition_option(request=request, action=action))
        hidden_answers.append(
            HiddenFactAnswer(
                fact_id=request.fact_id,
                access_path=action,
                evidence=answer,
            )
        )

    screening_case = PatientScreeningCase(
        case_id=f"integrated-ui:{patient_id}",
        disease_group=condition,
        trials=_screening_trials(
            trial_document=trial_document,
            group_id=group_id,
            candidate_ids=candidate_ids,
        ),
        initial_patient_state=patient_state,
        evidence_requests=requests,
        acquisition_options=options,
    )
    return IntegratedUIFixture(
        screening_case=screening_case,
        trial_sources=all_sources,
        candidate_hits=candidate_hits,
        hidden_answers=tuple(hidden_answers),
        search_conditions=(condition,),
        expected_candidate_trial_ids=tuple(expected_ids),
        trial_set_path=str(trial_set_path),
        patient_pairs_path=str(patient_pairs_path),
        generation_config_path=str(generation_config_path),
    )
