"""Build a public, synthetic case for the integrated terminal interface."""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..contracts import (
    CriterionLogic,
    EvidenceFact,
    EvidenceRequirement,
    EvidenceSourceType,
    NextAction,
    NextEvidenceRequest,
    NumericConstraint,
    PatientState,
    TrialSearchRank,
    TrialCriterion,
    VerificationStatus,
)
from ..environment import HiddenFactAnswer
from ..interactive.burden_contracts import AcquisitionMode, AcquisitionOption
from ..preparation import InMemoryCandidateSearch, TeamTrialCandidateSearch
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

_PATIENT_ANSWER_TOKENS = (
    "willing",
    "access",
    "speak_english",
    "english_speaking",
    "self_reported",
    "smoking",
    "diet",
    "right_handed",
    "refrain",
)


def _condition_for_group(document: dict[str, Any], group_id: str) -> str:
    for group in document.get("groups", []):
        if str(group.get("group_id")) != group_id:
            continue
        condition = group.get("search_condition") or group.get("group_label")
        if condition:
            return str(condition)
    if group_id in _GROUP_CONDITIONS:
        return _GROUP_CONDITIONS[group_id]
    raise ValueError(f"trial set has no search condition for group: {group_id}")


def _search_conditions_for_group(
    document: dict[str, Any], group_id: str
) -> tuple[str, ...]:
    for group in document.get("groups", []):
        if str(group.get("group_id")) != group_id:
            continue
        values = group.get("search_conditions")
        if values:
            return tuple(str(item) for item in values)
    return (_condition_for_group(document, group_id),)


def _categorical_value(value: object) -> float:
    normalized = str(value).strip().casefold()
    if normalized in {"present", "diagnosed", "true"}:
        return 1.0
    if normalized in {"absent", "not_diagnosed", "false"}:
        return 0.0
    raise ValueError(f"unsupported categorical state: {value}")


def _normalized_evidence_fact(
    fact: EvidenceFact,
    *,
    group_id: str,
    fact_aliases: dict[str, str],
) -> EvidenceFact:
    if fact.concept is None:
        return fact
    fact_code = fact.concept.rsplit(":", 1)[-1]
    normalized = fact_aliases.get(fact_code, fact_code)
    return fact.model_copy(update={"concept": f"{group_id}:{normalized}"})


def _sufficient_evidence_requirement(fact_code: str) -> EvidenceRequirement:
    patient_answer = any(token in fact_code for token in _PATIENT_ANSWER_TOKENS)
    return EvidenceRequirement(
        allowed_source_types=[
            EvidenceSourceType.PATIENT_REPORT
            if patient_answer
            else EvidenceSourceType.MEDICAL_RECORD
        ],
        allowed_verification_statuses=[
            VerificationStatus.REPORTED
            if patient_answer
            else VerificationStatus.VERIFIED
        ],
    )


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
    structured_criterion_count: int
    complete_protocol_coverage: bool
    search_pool_count: int
    search_scope_label: str
    search_top_k: int


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
                    _condition_for_group(document, group_id),
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
    fact_aliases: dict[str, str],
) -> list[ScreeningTrial]:
    metadata = {
        str(item["nct_id"]): item for item in trial_document["trials"]
    }
    criteria_by_trial: dict[str, list[TrialCriterion]] = defaultdict(list)
    for row in trial_document["criteria"]:
        trial_id = str(row["nct_id"])
        if trial_id not in candidate_ids:
            continue
        fact_code = fact_aliases.get(str(row["fact_code"]), str(row["fact_code"]))
        if row["operator"] is None:
            operator = "eq"
            threshold = _categorical_value(row.get("expected_value"))
            unit = "bool"
        else:
            operator = str(row["operator"])
            threshold = float(row["threshold"])
            unit = str(row["unit"])
        numeric = NumericConstraint(
            concept=f"{group_id}:{fact_code}",
            operator=operator,
            threshold=threshold,
            unit=unit,
        )
        trial = metadata[trial_id]
        if row.get("evidence_source_type") and row.get("verification_status"):
            evidence_requirement = EvidenceRequirement(
                allowed_source_types=[
                    EvidenceSourceType(str(row["evidence_source_type"]))
                ],
                allowed_verification_statuses=[
                    VerificationStatus(str(row["verification_status"]))
                ],
            )
        else:
            evidence_requirement = _sufficient_evidence_requirement(fact_code)
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
                evidence_requirement=evidence_requirement,
            )
        )
    return [
        ScreeningTrial(
            trial_id=trial_id,
            criteria=criteria_by_trial[trial_id],
            eligibility_logic=(
                None
                if metadata[trial_id].get("eligibility_logic") is None
                else CriterionLogic.model_validate(
                    metadata[trial_id]["eligibility_logic"]
                )
            ),
        )
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
    broad_corpus_path: str | Path | None = None,
    broad_search_top_k: int = 200,
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
    fact_aliases = {
        str(key): str(value)
        for key, value in generation_config.get("fact_aliases", {}).items()
    }
    condition = _condition_for_group(trial_document, group_id)
    search_conditions = _search_conditions_for_group(trial_document, group_id)
    all_sources = _trial_sources(trial_document)
    expected_ids = [str(item) for item in pair["trial_ids"]]
    if broad_corpus_path is None:
        group_sources = tuple(
            source
            for source in all_sources
            if condition.casefold()
            in {item.casefold() for item in source.conditions}
        )
        candidate_hits = tuple(
            InMemoryCandidateSearch(group_sources).search(
                [condition],
                top_k=len(expected_ids),
            )
        )
        search_pool_count = len(all_sources)
        search_scope_label = "평가자료에 포함된 공개 임상시험"
        applied_search_top_k = len(expected_ids)
    else:
        if broad_search_top_k < len(expected_ids):
            raise ValueError(
                "broad search depth must cover every declared candidate"
            )
        searcher = TeamTrialCandidateSearch(broad_corpus_path)
        broad_hits = searcher.search(
            search_conditions,
            top_k=broad_search_top_k,
        )
        expected_set = set(expected_ids)
        candidate_hits = tuple(
            item for item in broad_hits if item.source.trial_id in expected_set
        )
        search_pool_count = searcher.summary.included_trial_count
        search_scope_label = "모집 중·모집 예정 공개 임상시험"
        applied_search_top_k = broad_search_top_k
    candidate_ids = [item.source.trial_id for item in candidate_hits]
    if set(candidate_ids) != set(expected_ids):
        missing = sorted(set(expected_ids) - set(candidate_ids))
        raise ValueError(
            "candidate search did not recover the declared trials: "
            + ", ".join(missing)
        )

    pivotal = set(str(item) for item in pair["pivotal_fact_codes"])
    initial_facts = []
    for raw_fact in pair["insufficient_evidence_episode"]["evidence"]:
        fact = EvidenceFact.model_validate(raw_fact)
        fact_code = "" if fact.concept is None else fact.concept.rsplit(":", 1)[-1]
        if fact_code not in pivotal:
            initial_facts.append(
                _normalized_evidence_fact(
                    fact,
                    group_id=group_id,
                    fact_aliases=fact_aliases,
                )
            )
    evaluation_as_of = pairs_document.get("as_of", generation_config["as_of"])
    patient_state = PatientState(
        patient_id=patient_id,
        as_of=str(evaluation_as_of),
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
        answer_key = answer.concept.rsplit(":", 1)[-1]
        answer_by_key[answer_key] = _normalized_evidence_fact(
            answer,
            group_id=group_id,
            fact_aliases=fact_aliases,
        )

    raw_options = pair["insufficient_evidence_episode"].get(
        "acquisition_options"
    )
    if raw_options is None:
        options: list[AcquisitionOption] = []
    else:
        options = [AcquisitionOption.model_validate(item) for item in raw_options]
        requested_fact_ids = {request.fact_id for request in requests}
        options = [item for item in options if item.fact_id in requested_fact_ids]
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
        if raw_options is None:
            options.append(_acquisition_option(request=request, action=action))
        elif not any(item.fact_id == request.fact_id for item in options):
            raise ValueError(f"missing acquisition option for {request.fact_id}")
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
            fact_aliases=fact_aliases,
        ),
        initial_patient_state=patient_state,
        evidence_requests=requests,
        acquisition_options=options,
        candidate_ranking=[
            TrialSearchRank(
                trial_id=item.source.trial_id,
                rank=item.rank,
                score=item.score,
                retrieval_method=item.retrieval_method,
            )
            for item in candidate_hits
        ],
    )
    return IntegratedUIFixture(
        screening_case=screening_case,
        trial_sources=all_sources,
        candidate_hits=candidate_hits,
        hidden_answers=tuple(hidden_answers),
        search_conditions=search_conditions,
        expected_candidate_trial_ids=tuple(expected_ids),
        trial_set_path=str(trial_set_path),
        patient_pairs_path=str(patient_pairs_path),
        generation_config_path=str(generation_config_path),
        structured_criterion_count=len(trial_document["criteria"]),
        complete_protocol_coverage=bool(
            trial_document.get("complete_protocol_coverage", False)
        ),
        search_pool_count=search_pool_count,
        search_scope_label=search_scope_label,
        search_top_k=applied_search_top_k,
    )
