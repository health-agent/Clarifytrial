"""Public-criterion, synthetic-patient clarification benchmark."""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta, timezone
from itertools import product
from pathlib import Path
from statistics import mean
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..contracts import (
    ComparisonOperator,
    CriterionKind,
    EvidenceFact,
    EvidenceRequirement,
    EvidenceSourceType,
    NextAction,
    NumericConstraint,
    PatientState,
    TrialCriterion,
    VerificationStatus,
)
from ..datasets import CLARIFYTRIAL_V5_NCT_IDS
from ..environment import HiddenFactAnswer, PublicFactRequest
from .contracts import (
    ExactPolicyObjective,
    InteractiveCase,
    InteractiveHiddenFact,
    InteractiveTrial,
    PatientScenario,
    ScenarioDistribution,
    ScenarioFactAnswer,
)
from .exact_policy import ExactDecisionTreePolicy
from .policies import (
    ClarifyTrialRulePolicy,
    ImpactCostPolicy,
    NoQuestionPolicy,
    OutcomeEntropyPolicy,
    RandomQuestionPolicy,
    WidestImpactPolicy,
)
from .stress import _simulate, _weighted_metrics


_SOURCE_BY_ROUTE = {
    NextAction.ASK_PATIENT: (
        EvidenceSourceType.PATIENT_REPORT,
        VerificationStatus.REPORTED,
    ),
    NextAction.LOOKUP_RECORD: (
        EvidenceSourceType.MEDICAL_RECORD,
        VerificationStatus.VERIFIED,
    ),
    NextAction.REQUEST_VERIFICATION: (
        EvidenceSourceType.OFFICIAL_VERIFICATION,
        VerificationStatus.VERIFIED,
    ),
}

_ROUTE_COST = {
    NextAction.ASK_PATIENT: 1,
    NextAction.LOOKUP_RECORD: 2,
    NextAction.REQUEST_VERIFICATION: 3,
}


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublicFactSpec(_ConfigModel):
    code: str
    description: str
    unit: str
    route: NextAction
    values: list[float] = Field(min_length=2)


class PublicMaskSpec(_ConfigModel):
    mask_id: str
    visible_facts: list[str] = Field(min_length=1)


class PublicProfileSpec(_ConfigModel):
    profile_id: str
    split: Literal["development", "heldout"]
    values: dict[str, float]


class PublicCriterionSpec(_ConfigModel):
    id: str
    kind: CriterionKind
    source_statement: str
    fact: str
    operator: ComparisonOperator
    threshold: float
    max_age_days: int | None = Field(default=None, ge=0)


class PublicTrialSpec(_ConfigModel):
    nct_id: str
    title: str
    criteria: list[PublicCriterionSpec] = Field(min_length=1)


class PublicGroupSpec(_ConfigModel):
    group_id: str
    label: str
    masks: list[PublicMaskSpec] = Field(min_length=1)
    profiles: list[PublicProfileSpec] = Field(min_length=1)
    facts: list[PublicFactSpec] = Field(min_length=1)
    trials: list[PublicTrialSpec] = Field(min_length=1)

    @model_validator(mode="after")
    def references_are_valid(self) -> "PublicGroupSpec":
        fact_codes = {item.code for item in self.facts}
        if len(fact_codes) != len(self.facts):
            raise ValueError("fact codes must be unique")
        for mask in self.masks:
            if len(mask.visible_facts) != len(set(mask.visible_facts)):
                raise ValueError("visible facts must be unique")
            if not set(mask.visible_facts) < fact_codes:
                raise ValueError("each mask must leave at least one known and hidden fact")
        allowed_values = {item.code: set(item.values) for item in self.facts}
        for profile in self.profiles:
            if set(profile.values) != fact_codes:
                raise ValueError("every profile must define every fact")
            if any(
                value not in allowed_values[code]
                for code, value in profile.values.items()
            ):
                raise ValueError("profile value is outside the declared value grid")
        if any(
            criterion.fact not in fact_codes
            for trial in self.trials
            for criterion in trial.criteria
        ):
            raise ValueError("criterion refers to an unknown fact")
        return self


class PublicBenchmarkSpec(_ConfigModel):
    protocol_id: str
    source: str
    data_timestamp: str
    processed_date: str
    scope: str
    groups: list[PublicGroupSpec] = Field(min_length=1)


def load_public_benchmark_spec(path: str | Path) -> PublicBenchmarkSpec:
    return PublicBenchmarkSpec.model_validate_json(Path(path).read_text(encoding="utf-8"))


def _normal_tokens(text: str) -> set[str]:
    return {
        item
        for item in re.findall(r"[a-z0-9]+", text.lower())
        if len(item) > 1
    }


def audit_public_sources(
    spec: PublicBenchmarkSpec,
    source_cache: str | Path,
) -> list[dict[str, Any]]:
    """Check NCT IDs and wording coverage against the downloaded source records."""

    expected_ids = {
        group_id: set(nct_ids)
        for group_id, nct_ids in CLARIFYTRIAL_V5_NCT_IDS.items()
    }
    rows = []
    cache = Path(source_cache)
    for group in spec.groups:
        configured_ids = {item.nct_id for item in group.trials}
        if configured_ids != expected_ids.get(group.group_id):
            raise ValueError(f"configured NCT IDs differ for {group.group_id}")
        for trial in group.trials:
            record_path = cache / "records" / f"{trial.nct_id}.json"
            record = json.loads(record_path.read_text(encoding="utf-8"))
            actual_id = record["protocolSection"]["identificationModule"]["nctId"]
            if actual_id != trial.nct_id:
                raise ValueError(f"source record mismatch for {trial.nct_id}")
            eligibility = record["protocolSection"]["eligibilityModule"][
                "eligibilityCriteria"
            ]
            source_tokens = _normal_tokens(eligibility)
            for criterion in trial.criteria:
                criterion_tokens = _normal_tokens(criterion.source_statement)
                coverage = (
                    len(criterion_tokens & source_tokens) / len(criterion_tokens)
                    if criterion_tokens
                    else 0.0
                )
                if coverage < 0.65:
                    raise ValueError(
                        f"source wording coverage too low: {trial.nct_id}/{criterion.id}"
                    )
                rows.append(
                    {
                        "group_id": group.group_id,
                        "nct_id": trial.nct_id,
                        "criterion_id": criterion.id,
                        "source_token_coverage": coverage,
                        "source_url": f"https://clinicaltrials.gov/study/{trial.nct_id}",
                    }
                )
    return rows


def _source_for_fact(fact: PublicFactSpec):
    return _SOURCE_BY_ROUTE[fact.route]


def _fact_evidence(
    *,
    evidence_id: str,
    statement: str,
    source_location: str,
    group_id: str,
    fact: PublicFactSpec,
    value: float,
    as_of: datetime,
    provisional: bool = False,
) -> EvidenceFact:
    source_type, verification = _source_for_fact(fact)
    event_date = as_of.date() - timedelta(days=2)
    if provisional:
        event_date = as_of.date() - timedelta(days=365)
        if fact.route is NextAction.REQUEST_VERIFICATION:
            source_type = EvidenceSourceType.MEDICAL_RECORD
        elif fact.route is NextAction.LOOKUP_RECORD:
            source_type = EvidenceSourceType.PATIENT_REPORT
            verification = VerificationStatus.REPORTED
    return EvidenceFact(
        evidence_id=evidence_id,
        statement=statement,
        source_type=source_type,
        source_location=source_location,
        event_date=event_date,
        recorded_date=event_date,
        verification_status=verification,
        concept=f"{group_id}:{fact.code}",
        value=value,
        unit=fact.unit,
    )


def _trials(group: PublicGroupSpec) -> list[InteractiveTrial]:
    fact_by_code = {item.code: item for item in group.facts}
    trials = []
    for trial in group.trials:
        criteria = []
        for criterion in trial.criteria:
            fact = fact_by_code[criterion.fact]
            source_type, verification = _source_for_fact(fact)
            criteria.append(
                TrialCriterion(
                    criterion_id=f"{trial.nct_id}-{criterion.id}",
                    trial_id=trial.nct_id,
                    kind=criterion.kind,
                    statement=criterion.source_statement,
                    source_location=(
                        f"https://clinicaltrials.gov/study/{trial.nct_id}"
                        "#participation-criteria"
                    ),
                    numeric_constraint=NumericConstraint(
                        concept=f"{group.group_id}:{fact.code}",
                        operator=criterion.operator,
                        threshold=criterion.threshold,
                        unit=fact.unit,
                    ),
                    evidence_requirement=EvidenceRequirement(
                        max_age_days=criterion.max_age_days,
                        allowed_source_types=[source_type],
                        allowed_verification_statuses=[verification],
                    ),
                )
            )
        trials.append(InteractiveTrial(trial_id=trial.nct_id, criteria=criteria))
    return trials


def build_public_case(
    group: PublicGroupSpec,
    profile: PublicProfileSpec,
    mask: PublicMaskSpec,
    *,
    action_budget: int = 3,
) -> InteractiveCase:
    """Build one synthetic patient while preserving public criterion sources."""

    case_id = f"public-{profile.profile_id}-{mask.mask_id}"
    as_of = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
    diagnosis = EvidenceFact(
        evidence_id=f"{case_id}-diagnosis",
        statement=f"합성 환자의 {group.label} 진단이 기록되어 있다.",
        source_type=EvidenceSourceType.SYNTHETIC_CASE,
        source_location=f"synthetic-public-benchmark:{case_id}#diagnosis",
        event_date=date(2026, 8, 1),
        recorded_date=date(2026, 8, 1),
        verification_status=VerificationStatus.VERIFIED,
        concept="diagnosis_present",
        value=1,
        unit="bool",
    )
    visible_codes = set(mask.visible_facts)
    facts = [diagnosis]
    visible_ids = [diagnosis.evidence_id]
    hidden = []
    for fact in group.facts:
        value = profile.values[fact.code]
        evidence = _fact_evidence(
            evidence_id=f"{case_id}-{fact.code}-answer",
            statement=f"합성 환자 {fact.description}: {value:g} {fact.unit}",
            source_location=f"synthetic-public-benchmark:{case_id}#{fact.code}",
            group_id=group.group_id,
            fact=fact,
            value=value,
            as_of=as_of,
        )
        facts.append(evidence)
        if fact.code in visible_codes:
            visible_ids.append(evidence.evidence_id)
            continue
        if fact.route is not NextAction.ASK_PATIENT:
            digest = hashlib.sha256(
                f"{profile.profile_id}:{mask.mask_id}:{fact.code}".encode("utf-8")
            ).digest()
            provisional_value = fact.values[digest[0] % len(fact.values)]
            provisional = _fact_evidence(
                evidence_id=f"{case_id}-{fact.code}-provisional",
                statement=f"과거 또는 비공식 자료의 {fact.description}",
                source_location=(
                    f"synthetic-public-benchmark:{case_id}#{fact.code}-provisional"
                ),
                group_id=group.group_id,
                fact=fact,
                value=provisional_value,
                as_of=as_of,
                provisional=True,
            )
            facts.append(provisional)
            visible_ids.append(provisional.evidence_id)
        hidden.append(
            InteractiveHiddenFact(
                request=PublicFactRequest(
                    fact_id=f"{case_id}-{fact.code}",
                    description=fact.description,
                    available_actions=(fact.route,),
                ),
                answer=HiddenFactAnswer(
                    fact_id=f"{case_id}-{fact.code}",
                    access_path=fact.route,
                    evidence=evidence,
                ),
            )
        )
    if len(hidden) != 5:
        raise ValueError("public benchmark masks must hide exactly five facts")
    return InteractiveCase(
        case_id=case_id,
        disease_group=group.label,
        full_patient_state=PatientState(
            patient_id=f"synthetic-{profile.profile_id}",
            as_of=as_of,
            facts=facts,
        ),
        initial_visible_evidence_ids=visible_ids,
        trials=_trials(group),
        hidden_facts=hidden,
        action_budget=action_budget,
    )


def build_public_planning_distribution(
    group: PublicGroupSpec,
    case: InteractiveCase,
    profile: PublicProfileSpec,
    mask: PublicMaskSpec,
    *,
    reference_profiles: Sequence[PublicProfileSpec] | None = None,
) -> ScenarioDistribution:
    """Build full hidden-value support weighted only by development profiles."""

    fact_by_code = {item.code: item for item in group.facts}
    hidden_by_code = {
        item.request.fact_id.rsplit("-", 1)[-1]: item for item in case.hidden_facts
    }
    hidden_codes = [item.code for item in group.facts if item.code not in mask.visible_facts]
    references = list(reference_profiles or ()) or [
        item for item in group.profiles if item.split == "development"
    ]
    if not references:
        raise ValueError("planning distribution needs reference profiles")
    combinations = product(*(fact_by_code[code].values for code in hidden_codes))
    scenarios = []
    weights = []
    as_of = case.full_patient_state.as_of
    for position, values in enumerate(combinations):
        value_by_code = dict(zip(hidden_codes, values, strict=True))
        kernel_weight = 0.0
        for reference_profile in references:
            component = 1.0
            for fact in group.facts:
                observed_value = (
                    profile.values[fact.code]
                    if fact.code in mask.visible_facts
                    else value_by_code[fact.code]
                )
                if observed_value == reference_profile.values[fact.code]:
                    component *= 0.75
                else:
                    component *= 0.25 / (len(fact.values) - 1)
            kernel_weight += component / len(references)
        weights.append(kernel_weight)
        answers = []
        for code in hidden_codes:
            fact = fact_by_code[code]
            value = value_by_code[code]
            fact_id = hidden_by_code[code].request.fact_id
            answers.append(
                ScenarioFactAnswer(
                    fact_id=fact_id,
                    evidence=_fact_evidence(
                        evidence_id=(
                            f"planning-{case.case_id}-{code}-{position:05d}"
                        ),
                        statement=f"계획용 가능 값 {code}: {value:g}",
                        source_location=(
                            f"planning-public-benchmark:{case.case_id}#"
                            f"{code}-{position:05d}"
                        ),
                        group_id=group.group_id,
                        fact=fact,
                        value=value,
                        as_of=as_of,
                    ),
                )
            )
        scenarios.append(
            PatientScenario(
                scenario_id=f"{case.case_id}-scenario-{position:05d}",
                probability=1,
                answers=answers,
            )
        )
    total = sum(weights)
    return ScenarioDistribution(
        case_id=case.case_id,
        scenarios=[
            scenario.model_copy(update={"probability": weight / total})
            for scenario, weight in zip(scenarios, weights, strict=True)
        ],
    )


def _actual_scenario(case: InteractiveCase) -> PatientScenario:
    return PatientScenario(
        scenario_id=f"{case.case_id}-actual-hidden-state",
        probability=1,
        answers=[
            ScenarioFactAnswer(
                fact_id=item.request.fact_id,
                evidence=item.answer.evidence,
            )
            for item in case.hidden_facts
        ],
    )


def _policy_rows(
    group: PublicGroupSpec,
    profile: PublicProfileSpec,
    mask: PublicMaskSpec,
    *,
    seed: int,
    action_budget: int,
) -> list[dict[str, Any]]:
    case = build_public_case(
        group,
        profile,
        mask,
        action_budget=action_budget,
    )
    view = case.public_policy_view()
    initial = case.initial_patient_state()
    distribution = build_public_planning_distribution(group, case, profile, mask)
    policies = [
        NoQuestionPolicy(),
        RandomQuestionPolicy(seed),
        WidestImpactPolicy(),
        ImpactCostPolicy(),
        ClarifyTrialRulePolicy(),
        OutcomeEntropyPolicy(view, distribution),
        ExactDecisionTreePolicy(
            view,
            initial,
            distribution,
            ExactPolicyObjective.EXPECTED,
            planning_horizon=1,
        ),
        ExactDecisionTreePolicy(
            view,
            initial,
            distribution,
            ExactPolicyObjective.WORST_CASE,
            planning_horizon=1,
        ),
    ]
    actual = _actual_scenario(case)
    rows = []
    for policy in policies:
        result = _simulate(view, initial, actual, policy)
        rows.append(
            {
                "group_id": group.group_id,
                "disease_group": group.label,
                "profile_id": profile.profile_id,
                "split": profile.split,
                "mask_id": mask.mask_id,
                "case_id": case.case_id,
                "policy_id": policy.policy_id,
                "trial_recovery": result.trial_recovery,
                "candidate_recovery": result.candidate_recovery,
                "confirmation_recovery": result.confirmation_recovery,
                "unsafe_decisions": result.unsafe_decisions,
                "actions": result.actions,
                "route_cost": result.route_cost,
                "selected_fact_ids": list(result.selected_fact_ids),
                "selected_actions": list(result.selected_actions),
                "mismatched_trial_ids": list(result.mismatched_trial_ids),
                "planning_scenario_count": len(distribution.scenarios),
                "action_budget": action_budget,
            }
        )
    return rows


def _aggregate(
    rows: Iterable[dict[str, Any]],
    keys: Sequence[str],
) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in keys)].append(row)
    result = []
    for key_values, items in sorted(groups.items()):
        result.append(
            {
                **dict(zip(keys, key_values, strict=True)),
                "run_count": len(items),
                "mean_trial_recovery": mean(item["trial_recovery"] for item in items),
                "mean_candidate_recovery": mean(
                    item["candidate_recovery"] for item in items
                ),
                "mean_confirmation_recovery": mean(
                    item["confirmation_recovery"] for item in items
                ),
                "total_unsafe_decisions": sum(
                    item["unsafe_decisions"] for item in items
                ),
                "mean_actions": mean(item["actions"] for item in items),
                "mean_route_cost": mean(item["route_cost"] for item in items),
            }
        )
    return result


def _paired_summary(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    simple_ids = {
        "widest_impact",
        "impact_per_cost",
        "clarifytrial_rule_v1",
        "outcome_entropy",
    }
    development = _aggregate(
        (item for item in rows if item["split"] == "development"),
        ("policy_id",),
    )
    baseline = max(
        (item for item in development if item["policy_id"] in simple_ids),
        key=lambda item: item["mean_trial_recovery"],
    )
    baseline_id = baseline["policy_id"]
    heldout = [item for item in rows if item["split"] == "heldout"]
    profile_rows = _aggregate(
        heldout,
        ("profile_id", "policy_id"),
    )
    by_key = {
        (item["profile_id"], item["policy_id"]): item for item in profile_rows
    }
    profile_ids = sorted({item["profile_id"] for item in profile_rows})
    comparisons = []
    for candidate_id in (
        "exact_decision_tree_expected_horizon_1_v1",
        "exact_decision_tree_worst_case_horizon_1_v1",
    ):
        differences = [
            by_key[(profile_id, candidate_id)]["mean_trial_recovery"]
            - by_key[(profile_id, baseline_id)]["mean_trial_recovery"]
            for profile_id in profile_ids
        ]
        candidate_rows = [
            item
            for item in profile_rows
            if item["policy_id"] == candidate_id
        ]
        baseline_rows = [
            item for item in profile_rows if item["policy_id"] == baseline_id
        ]
        candidate_recovery = mean(
            item["mean_trial_recovery"] for item in candidate_rows
        )
        baseline_recovery = mean(
            item["mean_trial_recovery"] for item in baseline_rows
        )
        candidate_cost = mean(item["mean_route_cost"] for item in candidate_rows)
        baseline_cost = mean(item["mean_route_cost"] for item in baseline_rows)
        comparisons.append(
            {
                "candidate_policy_id": candidate_id,
                "baseline_policy_id": baseline_id,
                "base_patient_count": len(profile_ids),
                "candidate_recovery": candidate_recovery,
                "baseline_recovery": baseline_recovery,
                "mean_recovery_difference": mean(differences),
                "wins": sum(item > 1e-12 for item in differences),
                "ties": sum(abs(item) <= 1e-12 for item in differences),
                "losses": sum(item < -1e-12 for item in differences),
                "candidate_route_cost": candidate_cost,
                "baseline_route_cost": baseline_cost,
                "recovery_gate": candidate_recovery - baseline_recovery >= 0.05,
                "burden_gate": (
                    abs(candidate_recovery - baseline_recovery) <= 1e-12
                    and candidate_cost <= 0.8 * baseline_cost
                ),
            }
        )
    primary = next(
        item
        for item in comparisons
        if item["candidate_policy_id"]
        == "exact_decision_tree_worst_case_horizon_1_v1"
    )
    return {
        "baseline_selected_on_development": baseline_id,
        "primary_candidate": primary["candidate_policy_id"],
        "comparisons": comparisons,
        "primary_gate_passed": primary["recovery_gate"] or primary["burden_gate"],
    }


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def run_public_interactive_benchmark(
    config_path: str | Path,
    source_cache: str | Path,
    output_dir: str | Path,
    *,
    seed: int = 20_260_821,
    action_budget: int = 3,
    progress=None,
) -> Path:
    """Run 30 base patients and two masks using public trial criteria."""

    if action_budget not in {1, 2, 3}:
        raise ValueError("action_budget must be 1, 2, or 3")
    spec = load_public_benchmark_spec(config_path)
    source_audit = audit_public_sources(spec, source_cache)
    rows = []
    case_count = 0
    for group in spec.groups:
        for profile in group.profiles:
            for mask in group.masks:
                rows.extend(
                    _policy_rows(
                        group,
                        profile,
                        mask,
                        seed=seed + case_count,
                        action_budget=action_budget,
                    )
                )
                case_count += 1
                if progress is not None and case_count % 10 == 0:
                    progress(f"completed {case_count}/60 masked cases")
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    plan = {
        "protocol_id": spec.protocol_id,
        "source": spec.source,
        "source_data_timestamp": spec.data_timestamp,
        "config_path": str(config_path),
        "seed": seed,
        "base_patient_count": 30,
        "development_patient_count": 10,
        "heldout_patient_count": 20,
        "masks_per_patient": 2,
        "masked_case_count": 60,
        "candidate_trials_per_case": 5,
        "hidden_facts_per_case": 5,
        "action_budget": action_budget,
        "model_calls": 0,
        "scope": spec.scope,
        "medical_disclaimer": (
            "공개 시험 조건과 합성 환자를 사용한 연구용 사전 검토 실험이다. "
            "실제 참가 가능 여부는 시험 연구진과 의료진이 확인해야 한다."
        ),
    }
    _write_json(destination / "plan.json", plan)
    _write_json(destination / "source-audit.json", source_audit)
    (destination / "case-results.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rows),
        encoding="utf-8",
    )
    summary = {
        **plan,
        "policy_metrics": _aggregate(rows, ("split", "policy_id")),
        "disease_metrics": _aggregate(
            rows, ("split", "group_id", "policy_id")
        ),
        "paired_heldout": _paired_summary(rows),
        "source_audit_criterion_count": len(source_audit),
    }
    summary_path = destination / "summary.json"
    _write_json(summary_path, summary)
    return summary_path


def _visible_context_weight(
    group: PublicGroupSpec,
    profile: PublicProfileSpec,
    mask: PublicMaskSpec,
    references: Sequence[PublicProfileSpec],
) -> float:
    fact_by_code = {item.code: item for item in group.facts}
    weight = 0.0
    for reference in references:
        component = 1.0
        for code in mask.visible_facts:
            fact = fact_by_code[code]
            if profile.values[code] == reference.values[code]:
                component *= 0.75
            else:
                component *= 0.25 / (len(fact.values) - 1)
        weight += component / len(references)
    return weight


def _uniform_distribution(distribution: ScenarioDistribution) -> ScenarioDistribution:
    probability = 1 / len(distribution.scenarios)
    return ScenarioDistribution(
        case_id=distribution.case_id,
        scenarios=[
            item.model_copy(update={"probability": probability})
            for item in distribution.scenarios
        ],
    )


def _grid_metric_rows(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[
            (
                row["evaluation_distribution"],
                row["group_id"],
                row["mask_id"],
                row["policy_id"],
            )
        ].append(row)
    result = []
    metrics = (
        "expected_trial_recovery",
        "worst_trial_recovery",
        "expected_actions",
        "expected_route_cost",
    )
    for key, items in sorted(groups.items()):
        distribution, group_id, mask_id, policy_id = key
        total_weight = sum(item["context_weight"] for item in items)
        result.append(
            {
                "evaluation_distribution": distribution,
                "group_id": group_id,
                "mask_id": mask_id,
                "policy_id": policy_id,
                "visible_context_count": len(items),
                **{
                    metric: sum(
                        item["context_weight"] * item[metric] for item in items
                    )
                    / total_weight
                    for metric in metrics
                },
            }
        )
    return result


def _grid_policy_metrics(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["evaluation_distribution"], row["policy_id"])].append(row)
    result = []
    for (distribution, policy_id), items in sorted(groups.items()):
        result.append(
            {
                "evaluation_distribution": distribution,
                "policy_id": policy_id,
                "group_mask_count": len(items),
                "mean_trial_recovery": mean(
                    item["expected_trial_recovery"] for item in items
                ),
                "mean_worst_trial_recovery": mean(
                    item["worst_trial_recovery"] for item in items
                ),
                "mean_actions": mean(item["expected_actions"] for item in items),
                "mean_route_cost": mean(
                    item["expected_route_cost"] for item in items
                ),
            }
        )
    return result


def _grid_comparison(
    group_mask_rows: Sequence[dict[str, Any]],
    policy_rows: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Select the simple comparator on development and freeze it for evaluation."""

    simple_ids = {
        "widest_impact",
        "impact_per_cost",
        "clarifytrial_rule_v1",
        "outcome_entropy",
    }
    development = [
        item
        for item in policy_rows
        if item["evaluation_distribution"] == "development_kernel"
        and item["policy_id"] in simple_ids
    ]
    baseline = max(
        development,
        key=lambda item: (
            item["mean_trial_recovery"],
            -item["mean_route_cost"],
            item["policy_id"],
        ),
    )
    baseline_id = baseline["policy_id"]
    by_policy_distribution = {
        (item["evaluation_distribution"], item["policy_id"]): item
        for item in policy_rows
    }
    by_group_mask = {
        (
            item["evaluation_distribution"],
            item["group_id"],
            item["mask_id"],
            item["policy_id"],
        ): item
        for item in group_mask_rows
    }
    group_masks = sorted(
        {
            (item["group_id"], item["mask_id"])
            for item in group_mask_rows
        }
    )
    comparisons = []
    for distribution in ("heldout_kernel", "uniform_grid"):
        baseline_row = by_policy_distribution[(distribution, baseline_id)]
        for candidate_id in (
            "exact_decision_tree_expected_horizon_1_v1",
            "exact_decision_tree_worst_case_horizon_1_v1",
        ):
            candidate_row = by_policy_distribution[(distribution, candidate_id)]
            differences = [
                by_group_mask[
                    (distribution, group_id, mask_id, candidate_id)
                ]["expected_trial_recovery"]
                - by_group_mask[
                    (distribution, group_id, mask_id, baseline_id)
                ]["expected_trial_recovery"]
                for group_id, mask_id in group_masks
            ]
            recovery_difference = (
                candidate_row["mean_trial_recovery"]
                - baseline_row["mean_trial_recovery"]
            )
            recovery_equal = abs(recovery_difference) <= 1e-12
            recovery_gate = recovery_difference >= 0.05
            burden_gate = (
                recovery_equal
                and candidate_row["mean_route_cost"]
                <= 0.8 * baseline_row["mean_route_cost"]
            )
            comparisons.append(
                {
                    "evaluation_distribution": distribution,
                    "candidate_policy_id": candidate_id,
                    "baseline_policy_id": baseline_id,
                    "group_mask_count": len(group_masks),
                    "candidate_recovery": candidate_row["mean_trial_recovery"],
                    "baseline_recovery": baseline_row["mean_trial_recovery"],
                    "mean_recovery_difference": recovery_difference,
                    "wins": sum(item > 1e-12 for item in differences),
                    "ties": sum(abs(item) <= 1e-12 for item in differences),
                    "losses": sum(item < -1e-12 for item in differences),
                    "candidate_route_cost": candidate_row["mean_route_cost"],
                    "baseline_route_cost": baseline_row["mean_route_cost"],
                    "recovery_gate": recovery_gate,
                    "burden_gate": burden_gate,
                    "gate_passed": recovery_gate or burden_gate,
                }
            )
    primary = next(
        item
        for item in comparisons
        if item["evaluation_distribution"] == "heldout_kernel"
        and item["candidate_policy_id"]
        == "exact_decision_tree_worst_case_horizon_1_v1"
    )
    return {
        "baseline_selected_on_development": baseline_id,
        "comparisons": comparisons,
        "primary_candidate": primary["candidate_policy_id"],
        "primary_gate_passed": primary["gate_passed"],
    }


def run_public_grid_stress(
    config_path: str | Path,
    source_cache: str | Path,
    output_dir: str | Path,
    *,
    action_budget: int = 3,
    progress=None,
) -> Path:
    """Exhaust all declared value combinations under public criterion graphs."""

    if action_budget not in {1, 2, 3}:
        raise ValueError("action_budget must be 1, 2, or 3")
    spec = load_public_benchmark_spec(config_path)
    source_audit = audit_public_sources(spec, source_cache)
    rows = []
    context_count = 0
    scenario_evaluations = 0
    total_context_count = 0
    for group in spec.groups:
        fact_by_code = {item.code: item for item in group.facts}
        for mask in group.masks:
            count = 1
            for code in mask.visible_facts:
                count *= len(fact_by_code[code].values)
            total_context_count += count
    for group in spec.groups:
        fact_by_code = {item.code: item for item in group.facts}
        development = [item for item in group.profiles if item.split == "development"]
        heldout = [item for item in group.profiles if item.split == "heldout"]
        for mask in group.masks:
            visible_values = [
                fact_by_code[code].values for code in mask.visible_facts
            ]
            visible_combinations = list(product(*visible_values))
            for position, values in enumerate(visible_combinations):
                profile_values = {
                    fact.code: fact.values[0] for fact in group.facts
                }
                profile_values.update(dict(zip(mask.visible_facts, values, strict=True)))
                profile = PublicProfileSpec(
                    profile_id=(
                        f"grid-{group.group_id}-{mask.mask_id}-{position:03d}"
                    ),
                    split="development",
                    values=profile_values,
                )
                case = build_public_case(
                    group,
                    profile,
                    mask,
                    action_budget=action_budget,
                )
                initial = case.initial_patient_state()
                view = case.public_policy_view()
                planning = build_public_planning_distribution(
                    group,
                    case,
                    profile,
                    mask,
                    reference_profiles=development,
                )
                heldout_distribution = build_public_planning_distribution(
                    group,
                    case,
                    profile,
                    mask,
                    reference_profiles=heldout,
                )
                uniform_distribution = _uniform_distribution(planning)
                policies = [
                    NoQuestionPolicy(),
                    WidestImpactPolicy(),
                    ImpactCostPolicy(),
                    ClarifyTrialRulePolicy(),
                    OutcomeEntropyPolicy(view, planning),
                    ExactDecisionTreePolicy(
                        view,
                        initial,
                        planning,
                        ExactPolicyObjective.EXPECTED,
                        planning_horizon=1,
                    ),
                    ExactDecisionTreePolicy(
                        view,
                        initial,
                        planning,
                        ExactPolicyObjective.WORST_CASE,
                        planning_horizon=1,
                    ),
                ]
                development_context_weight = _visible_context_weight(
                    group, profile, mask, development
                )
                heldout_context_weight = _visible_context_weight(
                    group, profile, mask, heldout
                )
                uniform_context_weight = 1 / len(visible_combinations)
                for policy in policies:
                    simulations = [
                        _simulate(view, initial, scenario, policy)
                        for scenario in planning.scenarios
                    ]
                    scenario_evaluations += len(simulations)
                    for distribution_name, distribution, context_weight in (
                        (
                            "development_kernel",
                            planning,
                            development_context_weight,
                        ),
                        (
                            "heldout_kernel",
                            heldout_distribution,
                            heldout_context_weight,
                        ),
                        (
                            "uniform_grid",
                            uniform_distribution,
                            uniform_context_weight,
                        ),
                    ):
                        rows.append(
                            {
                                "evaluation_distribution": distribution_name,
                                "group_id": group.group_id,
                                "mask_id": mask.mask_id,
                                "context_id": case.case_id,
                                "policy_id": policy.policy_id,
                                "context_weight": context_weight,
                                **_weighted_metrics(simulations, distribution),
                            }
                        )
                context_count += 1
                if progress is not None and context_count % 10 == 0:
                    progress(
                        f"completed {context_count}/{total_context_count} "
                        "visible contexts"
                    )

    group_mask_metrics = _grid_metric_rows(rows)
    policy_metrics = _grid_policy_metrics(group_mask_metrics)
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    plan = {
        "protocol_id": "interactive-public-grid-stress-v1",
        "source_protocol_id": spec.protocol_id,
        "source_data_timestamp": spec.data_timestamp,
        "action_budget": action_budget,
        "visible_context_count": context_count,
        "scenario_policy_evaluations": scenario_evaluations,
        "policy_count": 7,
        "model_calls": 0,
        "scope": (
            "공개 조건의 선언된 값 조합을 전수 계산한 구조 민감도이며 임상 성능이 아니다."
        ),
    }
    _write_json(destination / "plan.json", plan)
    _write_json(destination / "source-audit.json", source_audit)
    (destination / "context-results.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rows),
        encoding="utf-8",
    )
    summary = {
        **plan,
        "group_mask_metrics": group_mask_metrics,
        "policy_metrics": policy_metrics,
        "comparison": _grid_comparison(group_mask_metrics, policy_metrics),
    }
    summary_path = destination / "summary.json"
    _write_json(summary_path, summary)
    return summary_path
