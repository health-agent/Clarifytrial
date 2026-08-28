"""Generate paired synthetic patients from the preliminary natural criteria."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..contracts import (
    ComparisonOperator,
    CriterionAssessment,
    CriterionKind,
    EvidenceFact,
    EvidenceRequirement,
    EvidenceSourceType,
    EvidenceSufficiency,
    NextAction,
    NextEvidenceRequest,
    NumericConstraint,
    PatientState,
    TrialCriterion,
    VerificationStatus,
)
from ..decision_rules import aggregate_trial_decision
from ..disclaimer import DEFAULT_MEDICAL_DISCLAIMER
from ..mechanical_checks import evaluate_criterion
from ..measurements import normalized_unit
from .integrity import portable_text_sha256


_TRUE_STATES = {"present", "diagnosed", "true"}
_FALSE_STATES = {"absent", "not_diagnosed", "false"}
_MEDICAL_DISCLAIMER = DEFAULT_MEDICAL_DISCLAIMER
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


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NaturalPatientGroupConfig(_ConfigModel):
    group_id: str = Field(min_length=1)
    development_profile_count: int = Field(ge=0)
    fixed_values: dict[str, float] = Field(default_factory=dict)
    pivotal_values: dict[str, float] = Field(min_length=5, max_length=5)


class NaturalPatientGenerationConfig(_ConfigModel):
    protocol_id: str = Field(min_length=1)
    profile_count_per_group: int = Field(default=10, ge=1)
    as_of: datetime
    fact_aliases: dict[str, str] = Field(default_factory=dict)
    groups: list[NaturalPatientGroupConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def groups_and_splits_are_valid(self) -> "NaturalPatientGenerationConfig":
        group_ids = [item.group_id for item in self.groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("patient generation groups must be unique")
        if any(
            item.development_profile_count > self.profile_count_per_group
            for item in self.groups
        ):
            raise ValueError("development count exceeds the group profile count")
        return self


def load_natural_patient_generation_config(
    path: str | Path,
) -> NaturalPatientGenerationConfig:
    return NaturalPatientGenerationConfig.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def _sha256(path: Path) -> str:
    return portable_text_sha256(path)


def _canonical_fact(code: str, aliases: Mapping[str, str]) -> str:
    return aliases.get(code, code)


def _categorical_value(value: object) -> float:
    normalized = str(value).strip().lower()
    if normalized in _TRUE_STATES:
        return 1.0
    if normalized in _FALSE_STATES:
        return 0.0
    raise ValueError(f"unsupported categorical state: {value}")


def _condition_met(value: float, operator: str, threshold: float) -> bool:
    comparisons = {
        "gt": value > threshold,
        "gte": value >= threshold,
        "lt": value < threshold,
        "lte": value <= threshold,
        "eq": value == threshold,
    }
    return comparisons[operator]


def _supports(row: Mapping[str, Any], value: float) -> bool:
    condition = _condition_met(value, str(row["operator"]), float(row["threshold"]))
    return condition if row["kind"] == "inclusion" else not condition


def _candidate_values(rows: Sequence[Mapping[str, Any]]) -> list[float]:
    if all(row.get("unit") == "bool" for row in rows):
        return [0.0, 1.0]
    thresholds = sorted({float(row["threshold"]) for row in rows})
    scale = max(1.0, max(abs(item) for item in thresholds) * 0.15)
    values = {threshold for threshold in thresholds}
    values.update({threshold - scale for threshold in thresholds})
    values.update({threshold + scale for threshold in thresholds})
    if len(thresholds) > 1:
        values.add((thresholds[0] + thresholds[-1]) / 2)
    return sorted(values)


def _best_and_alternative_values(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[float, float]:
    candidates = _candidate_values(rows)
    scored = [
        (sum(_supports(row, value) for row in rows), value) for value in candidates
    ]
    best_score, best_value = max(scored, key=lambda item: (item[0], -item[1]))
    alternatives = [item for item in scored if item[1] != best_value]
    if not alternatives:
        return best_value, best_value
    _, alternative = min(
        alternatives,
        key=lambda item: (item[0], abs(item[1] - best_value), item[1]),
    )
    if best_score == min(item[0] for item in alternatives):
        alternative = alternatives[-1][1]
    return best_value, alternative


def _fact_route(fact_code: str) -> tuple[NextAction, list[NextAction]]:
    if any(token in fact_code for token in _PATIENT_ANSWER_TOKENS):
        return NextAction.ASK_PATIENT, [
            NextAction.ASK_PATIENT,
            NextAction.LOOKUP_RECORD,
        ]
    return NextAction.LOOKUP_RECORD, [
        NextAction.LOOKUP_RECORD,
        NextAction.REQUEST_VERIFICATION,
    ]


def _sufficient_origin(
    route: NextAction,
) -> tuple[EvidenceSourceType, VerificationStatus]:
    if route is NextAction.ASK_PATIENT:
        return EvidenceSourceType.PATIENT_REPORT, VerificationStatus.REPORTED
    return EvidenceSourceType.MEDICAL_RECORD, VerificationStatus.VERIFIED


def _insufficient_origin(
    route: NextAction,
) -> tuple[EvidenceSourceType, VerificationStatus]:
    if route is NextAction.ASK_PATIENT:
        return EvidenceSourceType.PATIENT_REPORT, VerificationStatus.PENDING
    return EvidenceSourceType.PATIENT_REPORT, VerificationStatus.REPORTED


def _normalized_criteria(
    rows: Sequence[Mapping[str, Any]],
    aliases: Mapping[str, str],
) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        fact_code = _canonical_fact(str(row["fact_code"]), aliases)
        if row.get("operator") is None:
            operator = "eq"
            threshold = _categorical_value(row.get("expected_value"))
            unit = "bool"
        else:
            operator = str(row["operator"])
            threshold = float(row["threshold"])
            unit = str(row["unit"])
        result.append(
            {
                **row,
                "canonical_fact_code": fact_code,
                "operator": operator,
                "threshold": threshold,
                "unit": unit,
                "unit_key": normalized_unit(unit),
            }
        )
    return result


def _trial_criteria(
    rows: Sequence[Mapping[str, Any]],
) -> dict[str, list[TrialCriterion]]:
    result: dict[str, list[TrialCriterion]] = defaultdict(list)
    for row in rows:
        route, _ = _fact_route(str(row["canonical_fact_code"]))
        source_type, verification = _sufficient_origin(route)
        nct_id = str(row["nct_id"])
        result[nct_id].append(
            TrialCriterion(
                criterion_id=str(row["criterion_id"]),
                trial_id=nct_id,
                kind=CriterionKind(str(row["kind"])),
                statement=str(row["source_text"]),
                source_location=(
                    f"https://clinicaltrials.gov/study/{nct_id}"
                    "#participation-criteria"
                ),
                numeric_constraint=NumericConstraint(
                    concept=(
                        f"{row['group_id']}:{row['canonical_fact_code']}"
                    ),
                    operator=ComparisonOperator(str(row["operator"])),
                    threshold=float(row["threshold"]),
                    unit=str(row["unit"]),
                ),
                evidence_requirement=EvidenceRequirement(
                    allowed_source_types=[source_type],
                    allowed_verification_statuses=[verification],
                ),
            )
        )
    return dict(result)


def _evidence_fact(
    *,
    patient_id: str,
    group_id: str,
    fact_code: str,
    description: str,
    unit: str,
    value: float,
    as_of: datetime,
    sufficient: bool,
    pivotal: bool,
) -> EvidenceFact:
    route, _ = _fact_route(fact_code)
    source_type, verification = _sufficient_origin(route)
    suffix = "sufficient"
    if pivotal and not sufficient:
        source_type, verification = _insufficient_origin(route)
        suffix = "unverified"
    unit_id = hashlib.sha256(unit.encode("utf-8")).hexdigest()[:8]
    return EvidenceFact(
        evidence_id=f"{patient_id}:{fact_code}:{unit_id}:{suffix}",
        statement=f"합성 환자 {description}: {value:g} {unit}",
        source_type=source_type,
        source_location=f"synthetic-natural-evaluation:{patient_id}#{fact_code}",
        event_date=as_of.date() - timedelta(days=2),
        recorded_date=as_of.date() - timedelta(days=1),
        verification_status=verification,
        concept=f"{group_id}:{fact_code}",
        value=value,
        unit=unit,
    )


def _decisions(
    *,
    patient_state: PatientState,
    criteria_by_trial: Mapping[str, Sequence[TrialCriterion]],
    requests: Sequence[NextEvidenceRequest],
) -> list[dict[str, Any]]:
    decisions = []
    for trial_id, criteria in criteria_by_trial.items():
        assessments = []
        for criterion in criteria:
            result = evaluate_criterion(criterion, patient_state)
            related_missing = [
                item.fact_id
                for item in requests
                if criterion.criterion_id in item.related_criterion_ids
            ]
            assessments.append(
                CriterionAssessment(
                    criterion_id=criterion.criterion_id,
                    criterion_source_location=criterion.source_location,
                    clinical_status=result.clinical_status,
                    evidence_sufficiency=result.evidence_sufficiency,
                    evidence_ids=result.evidence_ids,
                    missing_information_ids=(
                        []
                        if result.evidence_sufficiency is EvidenceSufficiency.SUFFICIENT
                        else related_missing
                    ),
                    rationale="구조화된 합성값과 자료 상태를 코드로 비교했다.",
                )
            )
        related_requests = [
            item
            for item in requests
            if set(item.related_criterion_ids)
            & {criterion.criterion_id for criterion in criteria}
        ]
        decision = aggregate_trial_decision(
            trial_id=trial_id,
            criteria=criteria,
            assessments=assessments,
            pending_information=related_requests,
            available_evidence_ids=[item.evidence_id for item in patient_state.facts],
        )
        decisions.append(
            {
                "trial_id": trial_id,
                "candidate_status": decision.candidate_status.value,
                "confirmation_status": decision.confirmation_status.value,
                "pending_fact_ids": [
                    item.fact_id for item in decision.pending_information
                ],
            }
        )
    return decisions


def build_natural_evaluation_patient_pairs(
    *,
    trial_set_path: str | Path,
    generation_config_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Create 30 synthetic patients and two evidence states for each patient."""

    trial_set_path = Path(trial_set_path)
    generation_config_path = Path(generation_config_path)
    destination = Path(output_path)
    if destination.exists():
        raise FileExistsError("natural patient-pair output already exists")
    trial_set = json.loads(trial_set_path.read_text(encoding="utf-8"))
    config = load_natural_patient_generation_config(generation_config_path)
    source_criteria = trial_set.get("criteria")
    source_trials = trial_set.get("trials")
    if not isinstance(source_criteria, list) or not isinstance(source_trials, list):
        raise ValueError("trial set must contain trials and criteria")
    criteria = _normalized_criteria(source_criteria, config.fact_aliases)
    config_by_group = {item.group_id: item for item in config.groups}
    trial_groups = list(dict.fromkeys(str(item["group_id"]) for item in source_trials))
    if set(trial_groups) != set(config_by_group):
        raise ValueError("generation groups differ from the trial set")

    pairs = []
    group_summaries = []
    total_changed_confirmation_count = 0
    for group_id in trial_groups:
        group_config = config_by_group[group_id]
        group_rows = [item for item in criteria if item["group_id"] == group_id]
        trial_ids = [
            str(item["nct_id"])
            for item in source_trials
            if item["group_id"] == group_id
        ]
        criteria_by_trial = _trial_criteria(group_rows)
        if set(criteria_by_trial) != set(trial_ids):
            raise ValueError(f"criteria do not cover every trial in {group_id}")

        rows_by_key: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
        descriptions: dict[str, str] = {}
        for row in group_rows:
            key = (str(row["canonical_fact_code"]), str(row["unit_key"]))
            rows_by_key[key].append(row)
            descriptions.setdefault(
                str(row["canonical_fact_code"]),
                str(row.get("fact_description") or row["canonical_fact_code"]),
            )
        pivotal_codes = list(group_config.pivotal_values)
        missing_pivotal = set(pivotal_codes) - {item[0] for item in rows_by_key}
        if missing_pivotal:
            raise ValueError(
                f"pivotal facts are absent from {group_id}: "
                + ", ".join(sorted(missing_pivotal))
            )

        base_values: dict[tuple[str, str], float] = {}
        alternative_values: dict[tuple[str, str], float] = {}
        for key, fact_rows in rows_by_key.items():
            best, alternative = _best_and_alternative_values(fact_rows)
            unit = str(fact_rows[0]["unit"])
            fixed_key = f"{key[0]}|{unit}"
            if fixed_key in group_config.fixed_values:
                best = float(group_config.fixed_values[fixed_key])
                alternative = best
                if not all(_supports(row, best) for row in fact_rows):
                    raise ValueError(
                        f"fixed value does not support every criterion: {group_id}/{fixed_key}"
                    )
            elif key[0] in group_config.pivotal_values:
                best = float(group_config.pivotal_values[key[0]])
                if not all(_supports(row, best) for row in fact_rows):
                    raise ValueError(
                        f"pivotal value does not support every criterion: {group_id}/{key[0]}"
                    )
            base_values[key] = best
            alternative_values[key] = alternative
        nonpivotal_keys = sorted(
            key
            for key in rows_by_key
            if key[0] not in group_config.pivotal_values
            and alternative_values[key] != base_values[key]
        )
        if not nonpivotal_keys:
            raise ValueError(f"no profile variation is possible for {group_id}")

        affected_trials = {
            code: sorted(
                {
                    str(row["nct_id"])
                    for row in group_rows
                    if row["canonical_fact_code"] == code
                }
            )
            for code in pivotal_codes
        }
        if set().union(*(set(value) for value in affected_trials.values())) != set(
            trial_ids
        ):
            raise ValueError(f"pivotal facts do not cover all trials in {group_id}")

        group_changed_confirmation_count = 0
        for profile_index in range(config.profile_count_per_group):
            patient_id = f"natural-{group_id}-{profile_index + 1:02d}"
            split = (
                "development"
                if profile_index < group_config.development_profile_count
                else "heldout"
            )
            values = dict(base_values)
            if profile_index > 0:
                first = nonpivotal_keys[(profile_index - 1) % len(nonpivotal_keys)]
                values[first] = alternative_values[first]
            if profile_index >= 6 and len(nonpivotal_keys) > 1:
                second = nonpivotal_keys[
                    (profile_index + len(nonpivotal_keys) // 2)
                    % len(nonpivotal_keys)
                ]
                values[second] = alternative_values[second]

            sufficient_facts = []
            insufficient_facts = []
            clinical_values = []
            for key in sorted(rows_by_key):
                fact_code, _ = key
                unit = str(rows_by_key[key][0]["unit"])
                value = values[key]
                pivotal = fact_code in group_config.pivotal_values
                clinical_values.append(
                    {
                        "fact_code": fact_code,
                        "description": descriptions[fact_code],
                        "value": value,
                        "unit": unit,
                        "pivotal": pivotal,
                    }
                )
                sufficient_facts.append(
                    _evidence_fact(
                        patient_id=patient_id,
                        group_id=group_id,
                        fact_code=fact_code,
                        description=descriptions[fact_code],
                        unit=unit,
                        value=value,
                        as_of=config.as_of,
                        sufficient=True,
                        pivotal=pivotal,
                    )
                )
                insufficient_facts.append(
                    _evidence_fact(
                        patient_id=patient_id,
                        group_id=group_id,
                        fact_code=fact_code,
                        description=descriptions[fact_code],
                        unit=unit,
                        value=value,
                        as_of=config.as_of,
                        sufficient=False,
                        pivotal=pivotal,
                    )
                )

            requests = []
            for fact_code in pivotal_codes:
                _, actions = _fact_route(fact_code)
                related_ids = sorted(
                    str(row["criterion_id"])
                    for row in group_rows
                    if row["canonical_fact_code"] == fact_code
                )
                requests.append(
                    NextEvidenceRequest(
                        fact_id=f"{patient_id}:{fact_code}",
                        description=f"{descriptions[fact_code]} 확인",
                        related_criterion_ids=related_ids,
                        acceptable_actions=actions,
                        reason="현재 값은 보이지만 참가 조건을 확정할 근거가 부족하다.",
                    )
                )
            sufficient_state = PatientState(
                patient_id=patient_id,
                as_of=config.as_of,
                facts=sufficient_facts,
            )
            insufficient_state = PatientState(
                patient_id=patient_id,
                as_of=config.as_of,
                facts=insufficient_facts,
            )
            sufficient_values = [
                (item.concept, item.value, item.unit) for item in sufficient_facts
            ]
            insufficient_values = [
                (item.concept, item.value, item.unit) for item in insufficient_facts
            ]
            if sufficient_values != insufficient_values:
                raise ValueError(f"paired clinical values differ for {patient_id}")
            sufficient_decisions = _decisions(
                patient_state=sufficient_state,
                criteria_by_trial=criteria_by_trial,
                requests=[],
            )
            insufficient_decisions = _decisions(
                patient_state=insufficient_state,
                criteria_by_trial=criteria_by_trial,
                requests=requests,
            )
            candidate_a = {
                item["trial_id"]: item["candidate_status"]
                for item in sufficient_decisions
            }
            candidate_b = {
                item["trial_id"]: item["candidate_status"]
                for item in insufficient_decisions
            }
            if candidate_a != candidate_b:
                raise ValueError(f"paired candidate status differs for {patient_id}")
            changed_trials = sorted(
                item["trial_id"]
                for item in sufficient_decisions
                if item["confirmation_status"]
                != next(
                    row["confirmation_status"]
                    for row in insufficient_decisions
                    if row["trial_id"] == item["trial_id"]
                )
            )
            if not changed_trials:
                raise ValueError(f"paired confirmation status never changes for {patient_id}")
            sufficient_by_measure = {
                (item.concept, item.unit): item for item in sufficient_facts
            }
            recovered_facts = [
                sufficient_by_measure[(item.concept, item.unit)]
                if item.concept is not None
                and item.concept.rsplit(":", 1)[-1] in pivotal_codes
                else item
                for item in insufficient_facts
            ]
            recovered_decisions = _decisions(
                patient_state=PatientState(
                    patient_id=patient_id,
                    as_of=config.as_of,
                    facts=recovered_facts,
                ),
                criteria_by_trial=criteria_by_trial,
                requests=[],
            )
            if recovered_decisions != sufficient_decisions:
                raise ValueError(
                    f"verification does not recover sufficient decisions for {patient_id}"
                )
            group_changed_confirmation_count += len(changed_trials)
            total_changed_confirmation_count += len(changed_trials)
            pairs.append(
                {
                    "patient_id": patient_id,
                    "group_id": group_id,
                    "split": split,
                    "trial_ids": trial_ids,
                    "pivotal_fact_codes": pivotal_codes,
                    "clinical_values": clinical_values,
                    "sufficient_evidence_episode": {
                        "episode_id": f"{patient_id}:sufficient",
                        "evidence": [
                            item.model_dump(mode="json") for item in sufficient_facts
                        ],
                        "expected_trial_decisions": sufficient_decisions,
                    },
                    "insufficient_evidence_episode": {
                        "episode_id": f"{patient_id}:insufficient",
                        "evidence": [
                            item.model_dump(mode="json") for item in insufficient_facts
                        ],
                        "missing_information": [
                            item.model_dump(mode="json") for item in requests
                        ],
                        "verification_answers": [
                            item.model_dump(mode="json")
                            for item in sufficient_facts
                            if item.concept is not None
                            and item.concept.rsplit(":", 1)[-1] in pivotal_codes
                        ],
                        "expected_trial_decisions": insufficient_decisions,
                    },
                    "expected_pair_relation": {
                        "same_clinical_values": True,
                        "same_candidate_statuses": True,
                        "verification_recovers_sufficient_decisions": True,
                        "confirmation_changed_trial_ids": changed_trials,
                    },
                }
            )
        group_summaries.append(
            {
                "group_id": group_id,
                "profile_count": config.profile_count_per_group,
                "development_profile_count": group_config.development_profile_count,
                "heldout_profile_count": (
                    config.profile_count_per_group
                    - group_config.development_profile_count
                ),
                "trial_count": len(trial_ids),
                "criterion_count": len(group_rows),
                "pivotal_fact_codes": pivotal_codes,
                "pivotal_trial_coverage": affected_trials,
                "paired_confirmation_change_count": (
                    group_changed_confirmation_count
                ),
            }
        )

    development_count = sum(item["split"] == "development" for item in pairs)
    payload = {
        "status": "preliminary_ai_authored_synthetic_evaluation",
        "authority": (
            "Deterministically generated synthetic patients from AI-reviewed public "
            "criteria; not physician gold and not independent two-person consensus"
        ),
        "medical_data_notice": "All patient records in this file are synthetic.",
        "medical_disclaimer": _MEDICAL_DISCLAIMER,
        "trial_set_sha256": _sha256(trial_set_path),
        "generation_config_sha256": _sha256(generation_config_path),
        "clinical_value_rule": (
            "Each pair keeps every synthetic clinical value fixed and changes only "
            "whether five declared facts have confirmation-grade evidence"
        ),
        "evidence_policy": {
            "patient_answerable": "patient report with reported status",
            "record_based": "medical record with verified status",
            "insufficient_patient_answer": "patient report still pending",
            "insufficient_record_fact": "reported value without record verification",
            "protocol_boundary": (
                "This is an evaluation-stage evidence policy, not a claim that the "
                "trial protocol requires a specific record source"
            ),
        },
        "patient_count": len(pairs),
        "episode_count": len(pairs) * 2,
        "development_patient_count": development_count,
        "heldout_patient_count": len(pairs) - development_count,
        "paired_confirmation_change_count": total_changed_confirmation_count,
        "groups": group_summaries,
        "pairs": pairs,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "output": str(destination),
        "patient_count": payload["patient_count"],
        "episode_count": payload["episode_count"],
        "development_patient_count": development_count,
        "heldout_patient_count": payload["heldout_patient_count"],
        "paired_confirmation_change_count": total_changed_confirmation_count,
    }


def audit_natural_evaluation_patient_pairs(
    *,
    trial_set_path: str | Path,
    generation_config_path: str | Path,
    patient_pairs_path: str | Path,
) -> dict[str, Any]:
    """Recompute every stored episode and paired-state invariant."""

    trial_set_path = Path(trial_set_path)
    generation_config_path = Path(generation_config_path)
    patient_pairs_path = Path(patient_pairs_path)
    trial_set = json.loads(trial_set_path.read_text(encoding="utf-8"))
    document = json.loads(patient_pairs_path.read_text(encoding="utf-8"))
    config = load_natural_patient_generation_config(generation_config_path)
    if document.get("trial_set_sha256") != _sha256(trial_set_path):
        raise ValueError("patient pairs do not match the trial set")
    if document.get("generation_config_sha256") != _sha256(generation_config_path):
        raise ValueError("patient pairs do not match the generation config")
    if document.get("medical_data_notice") != (
        "All patient records in this file are synthetic."
    ):
        raise ValueError("synthetic patient notice is missing")
    if document.get("medical_disclaimer") != _MEDICAL_DISCLAIMER:
        raise ValueError("medical disclaimer is missing or changed")
    source_criteria = trial_set.get("criteria")
    if not isinstance(source_criteria, list):
        raise ValueError("trial set must contain criteria")
    criteria = _normalized_criteria(source_criteria, config.fact_aliases)
    criteria_by_group = {
        group.group_id: _trial_criteria(
            [item for item in criteria if item["group_id"] == group.group_id]
        )
        for group in config.groups
    }
    pairs = document.get("pairs")
    if not isinstance(pairs, list):
        raise ValueError("patient-pair document must contain pairs")

    patient_ids = []
    episode_ids = []
    development_count = 0
    changed_total = 0
    for pair in pairs:
        patient_id = str(pair["patient_id"])
        group_id = str(pair["group_id"])
        patient_ids.append(patient_id)
        split = pair.get("split")
        if split not in {"development", "heldout"}:
            raise ValueError(f"invalid patient split for {patient_id}")
        if split == "development":
            development_count += 1
        sufficient_episode = pair["sufficient_evidence_episode"]
        insufficient_episode = pair["insufficient_evidence_episode"]
        episode_ids.extend(
            [
                str(sufficient_episode["episode_id"]),
                str(insufficient_episode["episode_id"]),
            ]
        )
        sufficient_facts = [
            EvidenceFact.model_validate(item)
            for item in sufficient_episode["evidence"]
        ]
        insufficient_facts = [
            EvidenceFact.model_validate(item)
            for item in insufficient_episode["evidence"]
        ]
        if [
            (item.concept, item.value, item.unit) for item in sufficient_facts
        ] != [
            (item.concept, item.value, item.unit) for item in insufficient_facts
        ]:
            raise ValueError(f"paired clinical values differ for {patient_id}")
        requests = [
            NextEvidenceRequest.model_validate(item)
            for item in insufficient_episode["missing_information"]
        ]
        sufficient_decisions = _decisions(
            patient_state=PatientState(
                patient_id=patient_id,
                as_of=config.as_of,
                facts=sufficient_facts,
            ),
            criteria_by_trial=criteria_by_group[group_id],
            requests=[],
        )
        insufficient_decisions = _decisions(
            patient_state=PatientState(
                patient_id=patient_id,
                as_of=config.as_of,
                facts=insufficient_facts,
            ),
            criteria_by_trial=criteria_by_group[group_id],
            requests=requests,
        )
        if sufficient_decisions != sufficient_episode["expected_trial_decisions"]:
            raise ValueError(f"stored sufficient decisions differ for {patient_id}")
        if insufficient_decisions != insufficient_episode["expected_trial_decisions"]:
            raise ValueError(f"stored insufficient decisions differ for {patient_id}")
        sufficient_candidates = {
            item["trial_id"]: item["candidate_status"]
            for item in sufficient_decisions
        }
        insufficient_candidates = {
            item["trial_id"]: item["candidate_status"]
            for item in insufficient_decisions
        }
        if sufficient_candidates != insufficient_candidates:
            raise ValueError(f"paired candidate statuses differ for {patient_id}")
        changed_trials = sorted(
            item["trial_id"]
            for item in sufficient_decisions
            if item["confirmation_status"]
            != next(
                row["confirmation_status"]
                for row in insufficient_decisions
                if row["trial_id"] == item["trial_id"]
            )
        )
        if changed_trials != pair["expected_pair_relation"][
            "confirmation_changed_trial_ids"
        ]:
            raise ValueError(f"stored paired relation differs for {patient_id}")
        changed_total += len(changed_trials)
        verification_answers = [
            EvidenceFact.model_validate(item)
            for item in insufficient_episode["verification_answers"]
        ]
        answers_by_measure = {
            (item.concept, item.unit): item for item in verification_answers
        }
        recovered_facts = [
            answers_by_measure.get((item.concept, item.unit), item)
            for item in insufficient_facts
        ]
        recovered_decisions = _decisions(
            patient_state=PatientState(
                patient_id=patient_id,
                as_of=config.as_of,
                facts=recovered_facts,
            ),
            criteria_by_trial=criteria_by_group[group_id],
            requests=[],
        )
        if recovered_decisions != sufficient_decisions:
            raise ValueError(f"verification recovery differs for {patient_id}")

    if len(patient_ids) != len(set(patient_ids)):
        raise ValueError("patient IDs must be unique")
    if len(episode_ids) != len(set(episode_ids)):
        raise ValueError("episode IDs must be unique")
    expected_patient_count = len(config.groups) * config.profile_count_per_group
    expected_development_count = sum(
        item.development_profile_count for item in config.groups
    )
    if development_count != expected_development_count:
        raise ValueError("actual development patient count differs from the config")
    expected_counts = {
        "patient_count": expected_patient_count,
        "episode_count": expected_patient_count * 2,
        "development_patient_count": expected_development_count,
        "heldout_patient_count": expected_patient_count - expected_development_count,
        "paired_confirmation_change_count": changed_total,
    }
    for field, expected in expected_counts.items():
        if document.get(field) != expected:
            raise ValueError(f"patient-pair summary differs for {field}")
    return {
        "passed": True,
        **expected_counts,
        "candidate_status_mismatch_count": 0,
        "verification_recovery_mismatch_count": 0,
    }


__all__ = [
    "NaturalPatientGenerationConfig",
    "audit_natural_evaluation_patient_pairs",
    "build_natural_evaluation_patient_pairs",
    "load_natural_patient_generation_config",
]
