"""Build a broad synthetic benchmark for candidate preservation and recovery."""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

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
from ..io import atomic_write_text
from ..mechanical_checks import evaluate_criterion
from .integrity import portable_text_sha256


_MEDICAL_DISCLAIMER = "학생 과제용 실험 결과입니다."
AcquisitionModeValue = Literal[
    "internal_record",
    "outside_record",
    "patient_report",
    "existing_official_result",
    "new_noninvasive_test",
]


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MeasureSpec(_ConfigModel):
    fact_code: str = Field(min_length=1)
    description: str = Field(min_length=1)
    unit: str = Field(min_length=1)
    operator: Literal["gte", "lte"]
    thresholds: list[float] = Field(min_length=3, max_length=3)
    values: list[float] = Field(min_length=1)
    acquisition_mode: AcquisitionModeValue


class BinaryFactSpec(_ConfigModel):
    fact_code: str = Field(min_length=1)
    description: str = Field(min_length=1)
    values: list[float] = Field(min_length=1)

    @model_validator(mode="after")
    def values_are_binary(self) -> "BinaryFactSpec":
        if any(value not in {0.0, 1.0} for value in self.values):
            raise ValueError("binary fact values must be zero or one")
        return self


class BroadRescueGroupConfig(_ConfigModel):
    group_id: str = Field(min_length=1)
    group_label: str = Field(min_length=1)
    search_condition: str = Field(min_length=1)
    layout_variant: Literal["A", "B", "C"]
    measure: MeasureSpec
    history: BinaryFactSpec
    choice: BinaryFactSpec
    risk: BinaryFactSpec


class BroadRescueConfig(_ConfigModel):
    protocol_id: str = Field(min_length=1)
    as_of: datetime
    profiles_per_group: int = Field(ge=1)
    development_profiles_per_group: int = Field(ge=0)
    groups: list[BroadRescueGroupConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def structure_is_consistent(self) -> "BroadRescueConfig":
        if self.profiles_per_group != 5:
            raise ValueError("broad rescue v1 requires five profiles per group")
        if self.development_profiles_per_group > self.profiles_per_group:
            raise ValueError("development profiles exceed profiles per group")
        group_ids = [group.group_id for group in self.groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("group IDs must be unique")
        for group in self.groups:
            lengths = {
                len(group.measure.values),
                len(group.history.values),
                len(group.choice.values),
                len(group.risk.values),
            }
            if lengths != {self.profiles_per_group}:
                raise ValueError(
                    f"profile values differ from profiles_per_group: {group.group_id}"
                )
        return self


def load_broad_rescue_config(path: str | Path) -> BroadRescueConfig:
    return BroadRescueConfig.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def _source_policy(
    mode: AcquisitionModeValue,
) -> tuple[EvidenceSourceType, VerificationStatus, NextAction]:
    if mode == "patient_report":
        return (
            EvidenceSourceType.PATIENT_REPORT,
            VerificationStatus.REPORTED,
            NextAction.ASK_PATIENT,
        )
    if mode in {"internal_record", "outside_record"}:
        return (
            EvidenceSourceType.MEDICAL_RECORD,
            VerificationStatus.VERIFIED,
            NextAction.LOOKUP_RECORD,
        )
    return (
        EvidenceSourceType.OFFICIAL_VERIFICATION,
        VerificationStatus.VERIFIED,
        NextAction.REQUEST_VERIFICATION,
    )


def _acquisition_option(
    *,
    fact_id: str,
    mode: AcquisitionModeValue,
) -> dict[str, object]:
    _, _, action = _source_policy(mode)
    if mode == "patient_report":
        values = {
            "available_now": True,
            "expected_delay_hours": 0,
            "visit_required": False,
            "direct_cost_band": "none",
            "physical_burden_0_to_3": 0,
            "emotional_burden_0_to_3": 1,
            "medical_risk_0_to_3": 0,
            "treatment_disruption_0_to_3": 0,
            "source_note": "합성 환자가 직접 답할 수 있는 정보",
        }
    elif mode in {"internal_record", "outside_record"}:
        values = {
            "available_now": True,
            "expected_delay_hours": 2 if mode == "internal_record" else 24,
            "visit_required": False,
            "direct_cost_band": "none",
            "physical_burden_0_to_3": 0,
            "emotional_burden_0_to_3": 0,
            "medical_risk_0_to_3": 0,
            "treatment_disruption_0_to_3": 0,
            "source_note": "합성 평가에서 기존 기록으로 확인할 정보",
        }
    elif mode == "existing_official_result":
        values = {
            "available_now": True,
            "expected_delay_hours": 4,
            "visit_required": False,
            "direct_cost_band": "none",
            "physical_burden_0_to_3": 0,
            "emotional_burden_0_to_3": 0,
            "medical_risk_0_to_3": 0,
            "treatment_disruption_0_to_3": 0,
            "source_note": "합성 평가에서 이미 받은 공식 결과를 확인",
        }
    elif mode == "new_noninvasive_test":
        values = {
            "available_now": True,
            "expected_delay_hours": 48,
            "visit_required": True,
            "direct_cost_band": "medium",
            "physical_burden_0_to_3": 1,
            "emotional_burden_0_to_3": 1,
            "medical_risk_0_to_3": 1,
            "treatment_disruption_0_to_3": 0,
            "new_test_required": True,
            "requires_patient_choice": True,
            "requires_clinician_authorization": True,
            "source_note": "합성 평가에서 새 비침습 검사가 필요한 정보",
        }
    else:
        raise ValueError(f"unsupported maturity benchmark mode: {mode}")
    return {
        "option_id": f"{fact_id}:{mode}",
        "fact_id": fact_id,
        "action": action.value,
        "acquisition_mode": mode,
        **values,
    }


def _criterion_row(
    *,
    group: BroadRescueGroupConfig,
    trial_id: str,
    sequence: int,
    fact_code: str,
    description: str,
    mode: AcquisitionModeValue,
    kind: CriterionKind,
    operator: str | None,
    threshold: float | None,
    unit: str | None,
) -> dict[str, object]:
    source_type, verification, _ = _source_policy(mode)
    if operator is None:
        expectation = "없어야 한다" if kind is CriterionKind.EXCLUSION else "확인돼야 한다"
        source_text = f"합성 평가 조건: {description}이(가) {expectation}."
    else:
        source_text = (
            f"합성 평가 조건: {description} 값은 {operator} {threshold:g} {unit}이어야 한다."
        )
    return {
        "criterion_id": f"{trial_id}:criterion:{sequence:02d}",
        "group_id": group.group_id,
        "nct_id": trial_id,
        "kind": kind.value,
        "candidate_id": f"{trial_id}:candidate:{sequence:02d}",
        "source_text": source_text,
        "line_number": sequence,
        "confidence": "synthetic_declared",
        "fact_code": fact_code,
        "fact_description": description,
        "criterion_summary": source_text,
        "expected_value": "true" if operator is None else None,
        "operator": operator,
        "threshold": threshold,
        "unit": unit,
        "evidence_source_type": source_type.value,
        "verification_status": verification.value,
    }


def _group_rows(
    group: BroadRescueGroupConfig,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    diagnosis_code = f"{group.group_id}_diagnosis_confirmed"
    diagnosis_description = f"{group.group_label} 진단 확인"
    trial_rows = []
    criterion_rows = []
    layouts_by_variant = {
        "A": (
            (("diagnosis", None), ("measure", 0)),
            (("diagnosis", None), ("measure", 1), ("history", None)),
            (("diagnosis", None), ("measure", 2), ("choice", None)),
            (("diagnosis", None), ("history", None), ("risk", None)),
            (("diagnosis", None), ("choice", None), ("risk", None)),
        ),
        "B": (
            (("diagnosis", None), ("history", None)),
            (("diagnosis", None), ("measure", 0), ("choice", None)),
            (("diagnosis", None), ("measure", 1), ("risk", None)),
            (
                ("diagnosis", None),
                ("measure", 2),
                ("history", None),
                ("choice", None),
            ),
            (("diagnosis", None), ("history", None), ("risk", None)),
        ),
        "C": (
            (("diagnosis", None), ("measure", 0), ("risk", None)),
            (("diagnosis", None), ("choice", None)),
            (("diagnosis", None), ("measure", 1), ("history", None)),
            (
                ("diagnosis", None),
                ("measure", 2),
                ("choice", None),
                ("risk", None),
            ),
            (("diagnosis", None), ("history", None), ("choice", None)),
        ),
    }
    layouts = layouts_by_variant[group.layout_variant]
    for index, layout in enumerate(layouts, start=1):
        trial_id = f"SYN-{group.group_id.upper().replace('_', '-')}-{index:02d}"
        trial_rows.append(
            {
                "group_id": group.group_id,
                "group_label": group.group_label,
                "selection_slot": index,
                "nct_id": trial_id,
                "title": f"{group.group_label} 합성 시험 {index}",
                "study_url": f"synthetic://clarifytrial/{trial_id}",
            }
        )
        for sequence, (role, threshold_index) in enumerate(layout, start=1):
            if role == "diagnosis":
                row = _criterion_row(
                    group=group,
                    trial_id=trial_id,
                    sequence=sequence,
                    fact_code=diagnosis_code,
                    description=diagnosis_description,
                    mode="internal_record",
                    kind=CriterionKind.INCLUSION,
                    operator=None,
                    threshold=None,
                    unit=None,
                )
            elif role == "measure":
                assert threshold_index is not None
                row = _criterion_row(
                    group=group,
                    trial_id=trial_id,
                    sequence=sequence,
                    fact_code=group.measure.fact_code,
                    description=group.measure.description,
                    mode=group.measure.acquisition_mode,
                    kind=CriterionKind.INCLUSION,
                    operator=group.measure.operator,
                    threshold=group.measure.thresholds[threshold_index],
                    unit=group.measure.unit,
                )
            else:
                spec = getattr(group, role)
                mode = (
                    "patient_report"
                    if role in {"choice", "risk"}
                    else "internal_record"
                )
                row = _criterion_row(
                    group=group,
                    trial_id=trial_id,
                    sequence=sequence,
                    fact_code=spec.fact_code,
                    description=spec.description,
                    mode=mode,
                    kind=(
                        CriterionKind.EXCLUSION
                        if role == "risk"
                        else CriterionKind.INCLUSION
                    ),
                    operator=None,
                    threshold=None,
                    unit=None,
                )
            criterion_rows.append(row)
    return trial_rows, criterion_rows


def _trial_criteria(
    rows: Sequence[Mapping[str, object]],
) -> dict[str, list[TrialCriterion]]:
    criteria: dict[str, list[TrialCriterion]] = defaultdict(list)
    for row in rows:
        if row["operator"] is None:
            operator = ComparisonOperator.EQ
            threshold = 1.0
            unit = "bool"
        else:
            operator = ComparisonOperator(str(row["operator"]))
            threshold = float(row["threshold"])
            unit = str(row["unit"])
        criteria[str(row["nct_id"])].append(
            TrialCriterion(
                criterion_id=str(row["criterion_id"]),
                trial_id=str(row["nct_id"]),
                kind=CriterionKind(str(row["kind"])),
                statement=str(row["source_text"]),
                source_location=(
                    f"synthetic://clarifytrial/{row['nct_id']}"
                    f"#criterion={row['criterion_id']}"
                ),
                numeric_constraint=NumericConstraint(
                    concept=f"{row['group_id']}:{row['fact_code']}",
                    operator=operator,
                    threshold=threshold,
                    unit=unit,
                ),
                evidence_requirement=EvidenceRequirement(
                    allowed_source_types=[
                        EvidenceSourceType(str(row["evidence_source_type"]))
                    ],
                    allowed_verification_statuses=[
                        VerificationStatus(str(row["verification_status"]))
                    ],
                ),
            )
        )
    return dict(criteria)


def _decisions(
    *,
    patient_state: PatientState,
    criteria_by_trial: Mapping[str, Sequence[TrialCriterion]],
    requests: Sequence[NextEvidenceRequest],
) -> list[dict[str, object]]:
    decisions = []
    for trial_id, criteria in criteria_by_trial.items():
        assessments = []
        for criterion in criteria:
            check = evaluate_criterion(criterion, patient_state)
            missing = [
                request.fact_id
                for request in requests
                if criterion.criterion_id in request.related_criterion_ids
            ]
            assessments.append(
                CriterionAssessment(
                    criterion_id=criterion.criterion_id,
                    criterion_source_location=criterion.source_location,
                    clinical_status=check.clinical_status,
                    evidence_sufficiency=check.evidence_sufficiency,
                    evidence_ids=check.evidence_ids,
                    missing_information_ids=(
                        []
                        if check.evidence_sufficiency is EvidenceSufficiency.SUFFICIENT
                        else missing
                    ),
                    rationale="합성 상태표의 값과 조건을 코드로 비교했다.",
                )
            )
        pending = [
            request
            for request in requests
            if set(request.related_criterion_ids)
            & {criterion.criterion_id for criterion in criteria}
        ]
        decision = aggregate_trial_decision(
            trial_id=trial_id,
            criteria=criteria,
            assessments=assessments,
            pending_information=pending,
            available_evidence_ids=[fact.evidence_id for fact in patient_state.facts],
        )
        decisions.append(
            {
                "trial_id": trial_id,
                "candidate_status": decision.candidate_status.value,
                "confirmation_status": decision.confirmation_status.value,
                "pending_fact_ids": [item.fact_id for item in decision.pending_information],
            }
        )
    return decisions


def _fact(
    *,
    patient_id: str,
    group_id: str,
    fact_code: str,
    description: str,
    value: float,
    unit: str,
    mode: AcquisitionModeValue,
    as_of: datetime,
) -> EvidenceFact:
    source_type, verification, _ = _source_policy(mode)
    return EvidenceFact(
        evidence_id=f"{patient_id}:{fact_code}:answer",
        statement=f"합성 환자 {description}: {value:g} {unit}",
        source_type=source_type,
        source_location=f"synthetic-broad-rescue:{patient_id}#{fact_code}",
        event_date=as_of.date() - timedelta(days=2),
        recorded_date=as_of.date() - timedelta(days=1),
        verification_status=verification,
        concept=f"{group_id}:{fact_code}",
        value=value,
        unit=unit,
    )


def _build_documents(
    config: BroadRescueConfig,
    *,
    config_sha256: str,
) -> tuple[dict[str, object], dict[str, object]]:
    groups = []
    trials = []
    criteria = []
    pairs = []
    mode_counts: Counter[str] = Counter()
    for group in config.groups:
        group_trials, group_criteria = _group_rows(group)
        trials.extend(group_trials)
        criteria.extend(group_criteria)
        trial_ids = [str(item["nct_id"]) for item in group_trials]
        criteria_by_trial = _trial_criteria(group_criteria)
        diagnosis_code = f"{group.group_id}_diagnosis_confirmed"
        fact_specs = (
            (
                diagnosis_code,
                f"{group.group_label} 진단 확인",
                "bool",
                [1.0] * config.profiles_per_group,
                "internal_record",
            ),
            (
                group.measure.fact_code,
                group.measure.description,
                group.measure.unit,
                group.measure.values,
                group.measure.acquisition_mode,
            ),
            (
                group.history.fact_code,
                group.history.description,
                "bool",
                group.history.values,
                "internal_record",
            ),
            (
                group.choice.fact_code,
                group.choice.description,
                "bool",
                group.choice.values,
                "patient_report",
            ),
            (
                group.risk.fact_code,
                group.risk.description,
                "bool",
                group.risk.values,
                "patient_report",
            ),
        )
        related_by_fact: dict[str, list[str]] = defaultdict(list)
        for row in group_criteria:
            related_by_fact[str(row["fact_code"])].append(str(row["criterion_id"]))
        groups.append(
            {
                "group_id": group.group_id,
                "group_label": group.group_label,
                "search_condition": group.search_condition,
                "layout_variant": group.layout_variant,
                "trial_count": len(group_trials),
                "fact_codes": [item[0] for item in fact_specs],
            }
        )
        for profile_index in range(config.profiles_per_group):
            patient_id = f"broad-{group.group_id}-{profile_index + 1:02d}"
            facts = []
            initial_facts = []
            requests = []
            options = []
            clinical_values = []
            missing_patterns = (
                (group.measure.fact_code,),
                (diagnosis_code, group.measure.fact_code, group.history.fact_code),
                tuple(item[0] for item in fact_specs),
                (diagnosis_code, group.choice.fact_code, group.risk.fact_code),
                (group.measure.fact_code, group.risk.fact_code),
            )
            missing_codes = set(missing_patterns[profile_index])
            for fact_code, description, unit, values, mode in fact_specs:
                value = float(values[profile_index])
                fact = _fact(
                    patient_id=patient_id,
                    group_id=group.group_id,
                    fact_code=fact_code,
                    description=description,
                    value=value,
                    unit=unit,
                    mode=mode,
                    as_of=config.as_of,
                )
                facts.append(fact)
                if fact_code in missing_codes:
                    _, _, action = _source_policy(mode)
                    fact_id = f"{patient_id}:{fact_code}"
                    requests.append(
                        NextEvidenceRequest(
                            fact_id=fact_id,
                            description=f"{description} 확인",
                            related_criterion_ids=sorted(related_by_fact[fact_code]),
                            acceptable_actions=[action],
                            reason="이 정보가 없으면 연결된 시험의 참가 조건을 확인할 수 없다.",
                        )
                    )
                    options.append(_acquisition_option(fact_id=fact_id, mode=mode))
                    mode_counts[mode] += 1
                else:
                    initial_facts.append(fact)
                clinical_values.append(
                    {
                        "fact_code": fact_code,
                        "description": description,
                        "value": value,
                        "unit": unit,
                        "pivotal": True,
                        "acquisition_mode": mode,
                    }
                )
            full_state = PatientState(
                patient_id=patient_id,
                as_of=config.as_of,
                facts=facts,
            )
            initial_state = PatientState(
                patient_id=patient_id,
                as_of=config.as_of,
                facts=initial_facts,
            )
            full_decisions = _decisions(
                patient_state=full_state,
                criteria_by_trial=criteria_by_trial,
                requests=[],
            )
            initial_decisions = _decisions(
                patient_state=initial_state,
                criteria_by_trial=criteria_by_trial,
                requests=requests,
            )
            full_by_trial = {str(item["trial_id"]): item for item in full_decisions}
            initial_by_trial = {
                str(item["trial_id"]): item for item in initial_decisions
            }
            changed_candidates = sorted(
                trial_id
                for trial_id in trial_ids
                if full_by_trial[trial_id]["candidate_status"]
                != initial_by_trial[trial_id]["candidate_status"]
            )
            changed_confirmations = sorted(
                trial_id
                for trial_id in trial_ids
                if full_by_trial[trial_id]["confirmation_status"]
                != initial_by_trial[trial_id]["confirmation_status"]
            )
            pairs.append(
                {
                    "patient_id": patient_id,
                    "root_patient_id": patient_id,
                    "group_id": group.group_id,
                    "split": (
                        "development"
                        if profile_index < config.development_profiles_per_group
                        else "heldout"
                    ),
                    "trial_ids": trial_ids,
                    "pivotal_fact_codes": [
                        item[0] for item in fact_specs if item[0] in missing_codes
                    ],
                    "clinical_values": clinical_values,
                    "sufficient_evidence_episode": {
                        "episode_id": f"{patient_id}:complete",
                        "evidence": [item.model_dump(mode="json") for item in facts],
                        "expected_trial_decisions": full_decisions,
                    },
                    "insufficient_evidence_episode": {
                        "episode_id": f"{patient_id}:declared-missing",
                        "evidence": [
                            item.model_dump(mode="json") for item in initial_facts
                        ],
                        "missing_information": [
                            item.model_dump(mode="json") for item in requests
                        ],
                        "verification_answers": [
                            item.model_dump(mode="json")
                            for item in facts
                            if item.concept is not None
                            and item.concept.rsplit(":", 1)[-1] in missing_codes
                        ],
                        "acquisition_options": [
                            item for item in options
                        ],
                        "expected_trial_decisions": initial_decisions,
                    },
                    "expected_pair_relation": {
                        "same_clinical_values": True,
                        "candidate_changed_trial_ids": changed_candidates,
                        "confirmation_changed_trial_ids": changed_confirmations,
                        "all_missing_answers_are_declared": True,
                    },
                }
            )
    development_count = sum(pair["split"] == "development" for pair in pairs)
    full_decisions = [
        decision
        for pair in pairs
        for decision in pair["sufficient_evidence_episode"][
            "expected_trial_decisions"
        ]
    ]
    initial_decisions = [
        decision
        for pair in pairs
        for decision in pair["insufficient_evidence_episode"][
            "expected_trial_decisions"
        ]
    ]
    trial_set = {
        "status": "synthetic_maturity_benchmark",
        "authority": (
            "Deterministic synthetic trial criteria for software maturity testing; "
            "not ClinicalTrials.gov eligibility text"
        ),
        "medical_data_notice": "All trial criteria and patient records are synthetic.",
        "medical_disclaimer": _MEDICAL_DISCLAIMER,
        "protocol_id": config.protocol_id,
        "config_sha256": config_sha256,
        "group_count": len(groups),
        "trial_count": len(trials),
        "criterion_count": len(criteria),
        "groups": groups,
        "trials": trials,
        "criteria": criteria,
    }
    patient_pairs = {
        "status": "synthetic_maturity_benchmark",
        "authority": (
            "Deterministically generated broad synthetic patients; "
            "not clinical-performance gold"
        ),
        "medical_data_notice": "All patient records in this file are synthetic.",
        "medical_disclaimer": _MEDICAL_DISCLAIMER,
        "protocol_id": config.protocol_id,
        "config_sha256": config_sha256,
        "clinical_value_rule": (
            "One, two, three, or five declared facts are hidden, then revealed "
            "only through their declared acquisition paths; complete states "
            "include both eligible and ineligible trial outcomes"
        ),
        "patient_count": len(pairs),
        "episode_count": len(pairs) * 2,
        "development_patient_count": development_count,
        "heldout_patient_count": len(pairs) - development_count,
        "group_count": len(groups),
        "trial_count": len(trials),
        "patient_trial_pair_count": len(pairs) * 5,
        "initial_retained_not_confirmed_count": sum(
            decision["candidate_status"] == "retain"
            and decision["confirmation_status"] == "not_confirmed"
            for decision in initial_decisions
        ),
        "complete_confirmed_candidate_count": sum(
            decision["candidate_status"] == "retain"
            and decision["confirmation_status"] == "confirmed"
            for decision in full_decisions
        ),
        "complete_ineligible_count": sum(
            decision["candidate_status"] == "remove"
            and decision["confirmation_status"] == "ineligible"
            for decision in full_decisions
        ),
        "acquisition_mode_counts": dict(sorted(mode_counts.items())),
        "groups": groups,
        "pairs": pairs,
    }
    return trial_set, patient_pairs


def build_broad_rescue_dataset(
    *,
    config_path: str | Path,
    output_dir: str | Path,
) -> dict[str, object]:
    config_path = Path(config_path)
    destination = Path(output_dir)
    trial_path = destination / "trial_set.json"
    patient_path = destination / "patient_pairs.json"
    if trial_path.exists() or patient_path.exists():
        raise FileExistsError("broad rescue output already exists")
    config = load_broad_rescue_config(config_path)
    config_sha = portable_text_sha256(config_path)
    trial_set, patient_pairs = _build_documents(config, config_sha256=config_sha)
    atomic_write_text(
        trial_path,
        json.dumps(trial_set, ensure_ascii=False, indent=2) + "\n",
    )
    atomic_write_text(
        patient_path,
        json.dumps(patient_pairs, ensure_ascii=False, indent=2) + "\n",
    )
    return {
        "trial_set": str(trial_path),
        "patient_pairs": str(patient_path),
        "group_count": patient_pairs["group_count"],
        "trial_count": patient_pairs["trial_count"],
        "patient_count": patient_pairs["patient_count"],
        "patient_trial_pair_count": patient_pairs["patient_trial_pair_count"],
        "complete_confirmed_candidate_count": patient_pairs[
            "complete_confirmed_candidate_count"
        ],
        "complete_ineligible_count": patient_pairs["complete_ineligible_count"],
    }


def audit_broad_rescue_dataset(
    *,
    config_path: str | Path,
    trial_set_path: str | Path,
    patient_pairs_path: str | Path,
) -> dict[str, object]:
    config_path = Path(config_path)
    config = load_broad_rescue_config(config_path)
    expected_trial_set, expected_pairs = _build_documents(
        config,
        config_sha256=portable_text_sha256(config_path),
    )
    actual_trial_set = json.loads(Path(trial_set_path).read_text(encoding="utf-8"))
    actual_pairs = json.loads(Path(patient_pairs_path).read_text(encoding="utf-8"))
    if actual_trial_set != expected_trial_set:
        raise ValueError("broad rescue trial set differs from its declared config")
    if actual_pairs != expected_pairs:
        raise ValueError("broad rescue patients differ from their declared config")
    return {
        "passed": True,
        "group_count": actual_pairs["group_count"],
        "trial_count": actual_pairs["trial_count"],
        "patient_count": actual_pairs["patient_count"],
        "patient_trial_pair_count": actual_pairs["patient_trial_pair_count"],
        "initial_retained_not_confirmed_count": actual_pairs[
            "initial_retained_not_confirmed_count"
        ],
        "complete_confirmed_candidate_count": actual_pairs[
            "complete_confirmed_candidate_count"
        ],
        "complete_ineligible_count": actual_pairs["complete_ineligible_count"],
        "acquisition_mode_counts": actual_pairs["acquisition_mode_counts"],
    }


__all__ = [
    "BroadRescueConfig",
    "audit_broad_rescue_dataset",
    "build_broad_rescue_dataset",
    "load_broad_rescue_config",
]
