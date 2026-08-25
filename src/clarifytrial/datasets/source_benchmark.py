"""Build a protocol-grounded synthetic benchmark from selected public trials."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..contracts import (
    ComparisonOperator,
    CriterionAssessment,
    CriterionKind,
    CriterionLogic,
    EvidenceRequirement,
    EvidenceSufficiency,
    NextEvidenceRequest,
    NumericConstraint,
    PatientState,
    TrialCriterion,
)
from ..decision_rules import aggregate_trial_decision
from ..io import atomic_write_text
from ..mechanical_checks import evaluate_criterion
from ..preparation.team_trials import (
    TEAM_TRIALS_COMMIT,
    TEAM_TRIALS_SHA256,
    TEAM_TRIALS_URL,
    inspect_team_trial_corpus,
    iter_team_trial_records,
)
from .source_criteria import (
    AcquisitionModeValue,
    ExplicitLogicGroup,
    plausible_numeric_range,
    structure_selected_source_trials,
)
from .synthetic_evidence import acquisition_option, source_policy, synthetic_fact


_MEDICAL_DISCLAIMER = "학생 과제용 실험 결과입니다."


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceBenchmarkConfig(_ConfigModel):
    protocol_id: str = Field(min_length=1)
    as_of: datetime
    profiles_per_group: int = Field(default=5, ge=1)
    development_profiles_per_group: int = Field(default=2, ge=0)
    minimum_criteria_per_trial: int = Field(default=2, ge=1)
    maximum_criteria_per_trial: int = Field(default=5, ge=1)
    missing_fact_counts: list[int] = Field(default_factory=lambda: [1, 2, 3, 5, 5])
    value_profile_order: list[int] = Field(
        default_factory=lambda: [0, 2, 1, 3, 4]
    )
    search_conditions: dict[str, list[str]] = Field(min_length=1)
    missing_fact_strata: dict[str, list[str]] = Field(default_factory=dict)
    logic_groups: list[ExplicitLogicGroup] = Field(default_factory=list)

    @model_validator(mode="after")
    def declared_shape_is_consistent(self) -> "SourceBenchmarkConfig":
        if self.profiles_per_group != 5:
            raise ValueError("source benchmark v1 requires five profiles per group")
        if self.development_profiles_per_group > self.profiles_per_group:
            raise ValueError("development profile count exceeds group size")
        if len(self.missing_fact_counts) != self.profiles_per_group:
            raise ValueError("missing_fact_counts must define every profile")
        if sorted(self.value_profile_order) != list(range(self.profiles_per_group)):
            raise ValueError("value_profile_order must be a profile-index permutation")
        if not {1, 2, 3, 5}.issubset(self.missing_fact_counts):
            raise ValueError("missing fact counts must cover 1, 2, 3, and 5")
        if self.minimum_criteria_per_trial > self.maximum_criteria_per_trial:
            raise ValueError("minimum criteria exceeds maximum criteria")
        if any(not values for values in self.search_conditions.values()):
            raise ValueError("every disease group needs a search condition")
        unknown_strata = set(self.missing_fact_strata) - set(self.search_conditions)
        if unknown_strata:
            raise ValueError("missing-fact strata refer to unknown disease groups")
        if any(not values for values in self.missing_fact_strata.values()):
            raise ValueError("missing-fact strata cannot be empty")
        if any(
            len(values) != len(set(values))
            for values in self.missing_fact_strata.values()
        ):
            raise ValueError("missing-fact strata cannot repeat a fact")
        declared_logic_lines: set[tuple[str, int]] = set()
        for group in self.logic_groups:
            for line_number in group.source_line_numbers:
                key = (group.trial_id, line_number)
                if key in declared_logic_lines:
                    raise ValueError("logic groups cannot reuse a source line")
                declared_logic_lines.add(key)
        return self


def load_source_benchmark_config(path: str | Path) -> SourceBenchmarkConfig:
    return SourceBenchmarkConfig.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def _canonical_selection_hash(selection: Mapping[str, Any]) -> str:
    payload = [
        {"group_id": str(item["group_id"]), "nct_id": str(item["nct_id"])}
        for item in selection["selected_trials"]
    ]
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _stable_bucket(text: str, modulus: int) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:8], 16) % modulus


def _mode_for_fact(
    fact_code: str,
    declared_modes: Sequence[AcquisitionModeValue],
) -> AcquisitionModeValue:
    unique = set(declared_modes)
    if "patient_report" in unique:
        return "patient_report"
    if "existing_official_result" in unique:
        return (
            "new_noninvasive_test"
            if _stable_bucket(fact_code, 5) < 2
            else "existing_official_result"
        )
    if "outside_record" in unique:
        return "outside_record"
    return "internal_record"


def _enrich_criterion_rows(
    rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, AcquisitionModeValue]]:
    modes_by_fact: dict[str, list[AcquisitionModeValue]] = defaultdict(list)
    for row in rows:
        modes_by_fact[str(row["fact_code"])].append(
            str(row["acquisition_mode"])
        )
    selected_modes = {
        fact_code: _mode_for_fact(fact_code, modes)
        for fact_code, modes in modes_by_fact.items()
    }
    enriched = []
    for raw in rows:
        row = dict(raw)
        mode = selected_modes[str(row["fact_code"])]
        source_type, verification, _ = source_policy(mode)
        row["acquisition_mode"] = mode
        row["evidence_source_type"] = source_type.value
        row["verification_status"] = verification.value
        enriched.append(row)
    return enriched, selected_modes


def _trial_objects(
    *,
    rows: Sequence[Mapping[str, Any]],
    trials: Sequence[Mapping[str, Any]],
) -> tuple[
    dict[str, list[TrialCriterion]],
    dict[str, CriterionLogic],
]:
    criteria: dict[str, list[TrialCriterion]] = defaultdict(list)
    for row in rows:
        mode = str(row["acquisition_mode"])
        source_type, verification, _ = source_policy(mode)
        max_age_days = (
            14
            if mode in {"existing_official_result", "new_noninvasive_test"}
            else None
        )
        criteria[str(row["nct_id"])].append(
            TrialCriterion(
                criterion_id=str(row["criterion_id"]),
                trial_id=str(row["nct_id"]),
                kind=CriterionKind(str(row["kind"])),
                statement=str(row["source_text"]),
                source_location=str(row["source_location"]),
                required=True,
                numeric_constraint=NumericConstraint(
                    concept=f"{row['group_id']}:{row['fact_code']}",
                    operator=ComparisonOperator(str(row["operator"])),
                    threshold=float(row["threshold"]),
                    unit=str(row["unit"]),
                ),
                evidence_requirement=EvidenceRequirement(
                    max_age_days=max_age_days,
                    allowed_source_types=[source_type],
                    allowed_verification_statuses=[verification],
                ),
            )
        )
    logic = {
        str(item["nct_id"]): CriterionLogic.model_validate(
            item["eligibility_logic"]
        )
        for item in trials
    }
    return dict(criteria), logic


def _decision_rows(
    *,
    patient_state: PatientState,
    trial_ids: Sequence[str],
    criteria_by_trial: Mapping[str, Sequence[TrialCriterion]],
    logic_by_trial: Mapping[str, CriterionLogic],
    requests: Sequence[NextEvidenceRequest],
) -> list[dict[str, Any]]:
    decisions = []
    for trial_id in trial_ids:
        trial_criteria = criteria_by_trial[trial_id]
        assessments = []
        for criterion in trial_criteria:
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
                        if check.evidence_sufficiency
                        is EvidenceSufficiency.SUFFICIENT
                        else missing
                    ),
                    rationale="합성 상태표의 값과 공개 원문에서 옮긴 조건을 비교했다.",
                )
            )
        pending = [
            request
            for request in requests
            if set(request.related_criterion_ids)
            & {criterion.criterion_id for criterion in trial_criteria}
        ]
        decision = aggregate_trial_decision(
            trial_id=trial_id,
            criteria=trial_criteria,
            assessments=assessments,
            pending_information=pending,
            available_evidence_ids=[fact.evidence_id for fact in patient_state.facts],
            eligibility_logic=logic_by_trial[trial_id],
        )
        decisions.append(
            {
                "trial_id": trial_id,
                "candidate_status": decision.candidate_status.value,
                "confirmation_status": decision.confirmation_status.value,
                "pending_fact_ids": [
                    item.fact_id for item in decision.pending_information
                ],
                "logic_status": (
                    None
                    if decision.logic_evaluation is None
                    else decision.logic_evaluation.status.value
                ),
            }
        )
    return decisions


def _values_for_fact(
    rows: Sequence[Mapping[str, Any]],
) -> list[float]:
    units = {str(row["unit"]) for row in rows}
    if len(units) != 1:
        raise ValueError("one fact code cannot mix measurement units")
    unit = next(iter(units))
    if unit == "bool":
        kinds = {str(row["kind"]) for row in rows}
        if kinds == {CriterionKind.EXCLUSION.value}:
            return [0.0, 0.0, 1.0, 0.0, 1.0]
        if kinds == {CriterionKind.INCLUSION.value}:
            return [1.0, 1.0, 0.0, 1.0, 0.0]
        return [0.0, 1.0, 0.0, 1.0, 0.0]

    thresholds = sorted(float(row["threshold"]) for row in rows)
    low = thresholds[0]
    high = thresholds[-1]
    center = float(median(thresholds))
    span = max(1.0, high - low, abs(center) * 0.1)
    fact_code = str(rows[0]["fact_code"])
    plausible_range = plausible_numeric_range(fact_code, unit)
    if plausible_range is None:
        lower_bound, upper_bound = 0.0, float("inf")
    else:
        lower_bound, upper_bound = plausible_range
        span = min(span, max(1.0, (upper_bound - lower_bound) * 0.1))
    lower_outside = max(lower_bound, low - span)
    upper_outside = min(upper_bound, high + span)
    return [center, low, high, upper_outside, lower_outside]


def _fact_specs(
    *,
    group_id: str,
    rows: Sequence[Mapping[str, Any]],
    mode_by_fact: Mapping[str, AcquisitionModeValue],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        if str(row["group_id"]) == group_id:
            grouped[str(row["fact_code"])].append(row)
    specs = []
    for fact_code, fact_rows in grouped.items():
        descriptions = Counter(str(row["fact_description"]) for row in fact_rows)
        description = descriptions.most_common(1)[0][0]
        units = {str(row["unit"]) for row in fact_rows}
        if len(units) != 1:
            raise ValueError(f"fact {fact_code} mixes units")
        criterion_ids = sorted(str(row["criterion_id"]) for row in fact_rows)
        trial_ids = {str(row["nct_id"]) for row in fact_rows}
        specs.append(
            {
                "fact_code": fact_code,
                "description": description,
                "unit": next(iter(units)),
                "mode": mode_by_fact[fact_code],
                "values": _values_for_fact(fact_rows),
                "criterion_ids": criterion_ids,
                "related_trial_count": len(trial_ids),
            }
        )
    return sorted(specs, key=lambda item: str(item["fact_code"]))


def _profile_value(
    *,
    values: Sequence[float],
    profile_index: int,
    value_profile_order: Sequence[int],
) -> float:
    """Use a declared severity order shared by every clinical fact."""

    return float(values[value_profile_order[profile_index]])


def _mixed_impact_order(
    specs: Sequence[Mapping[str, Any]],
    *,
    stratified_fact_codes: Sequence[str] = (),
) -> list[str]:
    """Mix broadly shared and trial-specific facts without leaking answers."""

    ranked = sorted(
        specs,
        key=lambda item: (
            -int(item["related_trial_count"]),
            -len(item["criterion_ids"]),
            str(item["fact_code"]),
        ),
    )
    result = []
    left = 0
    right = len(ranked) - 1
    while left <= right:
        result.append(str(ranked[left]["fact_code"]))
        left += 1
        if left <= right:
            result.append(str(ranked[right]["fact_code"]))
            right -= 1
    known_codes = set(result)
    if not set(stratified_fact_codes).issubset(known_codes):
        missing = sorted(set(stratified_fact_codes) - known_codes)
        raise ValueError(
            "missing-fact stratum is absent from structured criteria: "
            + ", ".join(missing)
        )
    for offset, fact_code in enumerate(stratified_fact_codes):
        result.remove(fact_code)
        result.insert(min(1 + offset, len(result)), fact_code)
    return result


def _source_coverage(
    *,
    trials: Sequence[Mapping[str, Any]],
    criteria: Sequence[Mapping[str, Any]],
    records: Mapping[str, Any],
) -> dict[str, int]:
    used_by_trial: dict[str, set[int]] = defaultdict(set)
    for row in criteria:
        if row["source_field"] == "eligibility_text":
            used_by_trial[str(row["nct_id"])].add(int(row["line_number"]))
    total_lines = 0
    used_lines = 0
    for trial in trials:
        trial_id = str(trial["nct_id"])
        source_lines = {
            index
            for index, line in enumerate(
                records[trial_id].eligibility_text.splitlines(), start=1
            )
            if line.strip()
        }
        total_lines += len(source_lines)
        used_lines += len(source_lines & used_by_trial[trial_id])
    return {
        "nonempty_eligibility_source_line_count": total_lines,
        "structured_eligibility_source_line_count": used_lines,
        "unstructured_eligibility_source_line_count": total_lines - used_lines,
    }


def _build_documents(
    *,
    config: SourceBenchmarkConfig,
    selection: Mapping[str, Any],
    records: Mapping[str, Any],
    corpus_summary: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    trials, raw_criteria, method_counts = structure_selected_source_trials(
        selection=selection,
        records=records,
        minimum_criteria_per_trial=config.minimum_criteria_per_trial,
        maximum_criteria_per_trial=config.maximum_criteria_per_trial,
        logic_declarations=config.logic_groups,
    )
    criteria, mode_by_fact = _enrich_criterion_rows(raw_criteria)
    criteria_by_trial, logic_by_trial = _trial_objects(rows=criteria, trials=trials)
    group_metadata = {str(item["group_id"]): item for item in selection["groups"]}
    if set(group_metadata) != set(config.search_conditions):
        raise ValueError("search condition groups differ from selected disease groups")
    groups = []
    pairs = []
    mode_counts: Counter[str] = Counter()
    for group_id, metadata in group_metadata.items():
        group_trials = [item for item in trials if item["group_id"] == group_id]
        trial_ids = [str(item["nct_id"]) for item in group_trials]
        group_rows = [item for item in criteria if item["group_id"] == group_id]
        specs = _fact_specs(
            group_id=group_id,
            rows=group_rows,
            mode_by_fact=mode_by_fact,
        )
        if len(specs) < max(config.missing_fact_counts):
            raise ValueError(f"group {group_id} has too few distinct facts")
        missing_priority = _mixed_impact_order(
            specs,
            stratified_fact_codes=config.missing_fact_strata.get(group_id, ()),
        )
        search_conditions = config.search_conditions[group_id]
        groups.append(
            {
                "group_id": group_id,
                "group_label": str(metadata["group_label"]),
                "search_condition": search_conditions[0],
                "search_conditions": list(search_conditions),
                "layout_variant": "public_protocol",
                "trial_count": len(trial_ids),
                "fact_codes": [str(item["fact_code"]) for item in specs],
            }
        )
        for profile_index in range(config.profiles_per_group):
            patient_id = f"source-{group_id}-{profile_index + 1:02d}"
            missing_count = config.missing_fact_counts[profile_index]
            missing_codes = set(missing_priority[:missing_count])
            all_facts = []
            initial_facts = []
            requests = []
            options = []
            clinical_values = []
            for spec in specs:
                fact_code = str(spec["fact_code"])
                mode = str(spec["mode"])
                value = _profile_value(
                    values=spec["values"],
                    profile_index=profile_index,
                    value_profile_order=config.value_profile_order,
                )
                fact = synthetic_fact(
                    patient_id=patient_id,
                    group_id=group_id,
                    fact_code=fact_code,
                    description=str(spec["description"]),
                    value=value,
                    unit=str(spec["unit"]),
                    mode=mode,
                    as_of=config.as_of,
                    source_namespace="synthetic-public-protocol",
                )
                all_facts.append(fact)
                if fact_code in missing_codes:
                    _, _, action = source_policy(mode)
                    fact_id = f"{patient_id}:{fact_code}"
                    requests.append(
                        NextEvidenceRequest(
                            fact_id=fact_id,
                            description=f"{spec['description']} 확인",
                            related_criterion_ids=list(spec["criterion_ids"]),
                            acceptable_actions=[action],
                            reason=(
                                "이 정보가 없으면 공개 원문에서 옮긴 조건을 "
                                "현재 자료로 확인할 수 없다."
                            ),
                        )
                    )
                    options.append(acquisition_option(fact_id=fact_id, mode=mode))
                    mode_counts[mode] += 1
                else:
                    initial_facts.append(fact)
                clinical_values.append(
                    {
                        "fact_code": fact_code,
                        "description": str(spec["description"]),
                        "value": value,
                        "unit": str(spec["unit"]),
                        "pivotal": fact_code in missing_codes,
                        "acquisition_mode": mode,
                    }
                )
            full_state = PatientState(
                patient_id=patient_id,
                as_of=config.as_of,
                facts=all_facts,
            )
            initial_state = PatientState(
                patient_id=patient_id,
                as_of=config.as_of,
                facts=initial_facts,
            )
            full_decisions = _decision_rows(
                patient_state=full_state,
                trial_ids=trial_ids,
                criteria_by_trial=criteria_by_trial,
                logic_by_trial=logic_by_trial,
                requests=[],
            )
            initial_decisions = _decision_rows(
                patient_state=initial_state,
                trial_ids=trial_ids,
                criteria_by_trial=criteria_by_trial,
                logic_by_trial=logic_by_trial,
                requests=requests,
            )
            full_by_trial = {item["trial_id"]: item for item in full_decisions}
            initial_by_trial = {
                item["trial_id"]: item for item in initial_decisions
            }
            pairs.append(
                {
                    "patient_id": patient_id,
                    "root_patient_id": patient_id,
                    "group_id": group_id,
                    "split": (
                        "development"
                        if profile_index < config.development_profiles_per_group
                        else "heldout"
                    ),
                    "trial_ids": trial_ids,
                    "pivotal_fact_codes": [
                        str(item["fact_code"])
                        for item in specs
                        if item["fact_code"] in missing_codes
                    ],
                    "clinical_values": clinical_values,
                    "sufficient_evidence_episode": {
                        "episode_id": f"{patient_id}:complete",
                        "evidence": [item.model_dump(mode="json") for item in all_facts],
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
                            for item in all_facts
                            if item.concept is not None
                            and item.concept.rsplit(":", 1)[-1] in missing_codes
                        ],
                        "acquisition_options": options,
                        "expected_trial_decisions": initial_decisions,
                    },
                    "expected_pair_relation": {
                        "same_clinical_values": True,
                        "candidate_changed_trial_ids": sorted(
                            trial_id
                            for trial_id in trial_ids
                            if full_by_trial[trial_id]["candidate_status"]
                            != initial_by_trial[trial_id]["candidate_status"]
                        ),
                        "confirmation_changed_trial_ids": sorted(
                            trial_id
                            for trial_id in trial_ids
                            if full_by_trial[trial_id]["confirmation_status"]
                            != initial_by_trial[trial_id]["confirmation_status"]
                        ),
                        "all_missing_answers_are_declared": True,
                    },
                }
            )

    full_decisions = [
        decision
        for pair in pairs
        for decision in pair["sufficient_evidence_episode"]["expected_trial_decisions"]
    ]
    initial_decisions = [
        decision
        for pair in pairs
        for decision in pair["insufficient_evidence_episode"]["expected_trial_decisions"]
    ]
    coverage = _source_coverage(
        trials=trials,
        criteria=criteria,
        records=records,
    )
    selection_hash = _canonical_selection_hash(selection)
    trial_set = {
        "status": "public_protocol_derived_benchmark",
        "authority": (
            "Selected criteria are copied or conservatively structured from a "
            "pinned public ClinicalTrials.gov snapshot; they are not the complete "
            "eligibility protocol."
        ),
        "medical_data_notice": "Trial sources are public; every patient is synthetic.",
        "medical_disclaimer": _MEDICAL_DISCLAIMER,
        "protocol_id": config.protocol_id,
        "source_snapshot": {
            "url": TEAM_TRIALS_URL,
            "commit": TEAM_TRIALS_COMMIT,
            "sha256": TEAM_TRIALS_SHA256,
            "selected_trial_ids_sha256": selection_hash,
            "row_count": corpus_summary["row_count"],
            "included_trial_count": corpus_summary["included_trial_count"],
        },
        "supported_scope": (
            "Structured age fields, exact pregnancy or active-infection exclusions, "
            "single numeric comparisons, simple source predicates, and explicitly "
            "declared N-of-M source blocks only."
        ),
        "source_coverage": coverage,
        "structuring_method_counts": method_counts,
        "group_count": len(groups),
        "trial_count": len(trials),
        "criterion_count": len(criteria),
        "logic_trial_count": sum(
            bool(item["eligibility_logic"]["children"]) for item in trials
        ),
        "explicit_non_all_logic_trial_count": len(
            {item.trial_id for item in config.logic_groups}
        ),
        "explicit_non_all_logic_group_count": len(config.logic_groups),
        "groups": groups,
        "trials": trials,
        "criteria": criteria,
    }
    development_count = sum(pair["split"] == "development" for pair in pairs)
    patient_pairs = {
        "status": "synthetic_patients_for_public_protocol_subset",
        "authority": (
            "Deterministic synthetic facts evaluated only against the declared "
            "public-protocol subset; not clinical-performance gold."
        ),
        "medical_data_notice": "All patient records in this file are synthetic.",
        "medical_disclaimer": _MEDICAL_DISCLAIMER,
        "protocol_id": config.protocol_id,
        "as_of": config.as_of.isoformat(),
        "synthetic_value_assignment": "declared_profile_value_order",
        "value_profile_order": list(config.value_profile_order),
        "selected_trial_ids_sha256": selection_hash,
        "patient_count": len(pairs),
        "episode_count": len(pairs) * 2,
        "development_patient_count": development_count,
        "heldout_patient_count": len(pairs) - development_count,
        "group_count": len(groups),
        "trial_count": len(trials),
        "patient_trial_pair_count": sum(len(pair["trial_ids"]) for pair in pairs),
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
    if patient_pairs["complete_confirmed_candidate_count"] == 0:
        raise ValueError("source benchmark has no confirmed candidate outcome")
    if patient_pairs["complete_ineligible_count"] == 0:
        raise ValueError("source benchmark has no ineligible outcome")
    return trial_set, patient_pairs


def build_source_benchmark(
    *,
    config_path: str | Path,
    selection_path: str | Path,
    corpus_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    config = load_source_benchmark_config(config_path)
    selection = json.loads(Path(selection_path).read_text(encoding="utf-8"))
    corpus = inspect_team_trial_corpus(corpus_path)
    if corpus.source_sha256 != TEAM_TRIALS_SHA256:
        raise ValueError("public trial snapshot does not match the pinned SHA256")
    records = {item.nct_id: item for item in iter_team_trial_records(corpus_path)}
    trial_set, patient_pairs = _build_documents(
        config=config,
        selection=selection,
        records=records,
        corpus_summary=corpus.model_dump(mode="json"),
    )
    destination = Path(output_dir)
    trial_path = destination / "trial_set.json"
    pair_path = destination / "patient_pairs.json"
    if trial_path.exists() or pair_path.exists():
        raise FileExistsError("source benchmark output already exists")
    destination.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        trial_path,
        json.dumps(trial_set, ensure_ascii=False, indent=2) + "\n",
    )
    atomic_write_text(
        pair_path,
        json.dumps(patient_pairs, ensure_ascii=False, indent=2) + "\n",
    )
    return {
        "trial_set": str(trial_path),
        "patient_pairs": str(pair_path),
        "group_count": patient_pairs["group_count"],
        "trial_count": patient_pairs["trial_count"],
        "criterion_count": trial_set["criterion_count"],
        "patient_count": patient_pairs["patient_count"],
        "complete_confirmed_candidate_count": patient_pairs[
            "complete_confirmed_candidate_count"
        ],
        "complete_ineligible_count": patient_pairs["complete_ineligible_count"],
    }


def audit_source_benchmark(
    *,
    config_path: str | Path,
    selection_path: str | Path,
    corpus_path: str | Path,
    trial_set_path: str | Path,
    patient_pairs_path: str | Path,
) -> dict[str, Any]:
    config = load_source_benchmark_config(config_path)
    selection = json.loads(Path(selection_path).read_text(encoding="utf-8"))
    corpus = inspect_team_trial_corpus(corpus_path)
    if corpus.source_sha256 != TEAM_TRIALS_SHA256:
        raise ValueError("public trial snapshot does not match the pinned SHA256")
    records = {item.nct_id: item for item in iter_team_trial_records(corpus_path)}
    expected_trials, expected_pairs = _build_documents(
        config=config,
        selection=selection,
        records=records,
        corpus_summary=corpus.model_dump(mode="json"),
    )
    actual_trials = json.loads(Path(trial_set_path).read_text(encoding="utf-8"))
    actual_pairs = json.loads(Path(patient_pairs_path).read_text(encoding="utf-8"))
    if actual_trials != expected_trials:
        raise ValueError("source benchmark trial set differs from its inputs")
    if actual_pairs != expected_pairs:
        raise ValueError("source benchmark patients differ from their inputs")
    return {
        "passed": True,
        "group_count": actual_pairs["group_count"],
        "trial_count": actual_pairs["trial_count"],
        "criterion_count": actual_trials["criterion_count"],
        "patient_count": actual_pairs["patient_count"],
        "complete_confirmed_candidate_count": actual_pairs[
            "complete_confirmed_candidate_count"
        ],
        "complete_ineligible_count": actual_pairs["complete_ineligible_count"],
        "source_coverage": actual_trials["source_coverage"],
        "structuring_method_counts": actual_trials["structuring_method_counts"],
    }


__all__ = [
    "SourceBenchmarkConfig",
    "audit_source_benchmark",
    "build_source_benchmark",
    "load_source_benchmark_config",
]
