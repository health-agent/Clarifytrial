"""Large deterministic stress benchmark for clarification policies."""

from __future__ import annotations

import json
import random
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import mean
from typing import Any

from ..contracts import (
    AgentAction,
    CandidateStatus,
    ComparisonOperator,
    ConfirmationStatus,
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
from ..disclaimer import DEFAULT_MEDICAL_DISCLAIMER
from ..environment import HiddenFactAnswer, PublicFactRequest
from .contracts import (
    ExactPolicyObjective,
    InteractiveCase,
    InteractiveHiddenFact,
    InteractivePolicyView,
    InteractiveTrial,
    PatientScenario,
    ScenarioDistribution,
)
from .exact_policy import ExactDecisionTreePolicy, build_uniform_binary_scenarios
from .oracle import evaluate_policy_view
from .policies import (
    ClarifyTrialRulePolicy,
    ImpactCostPolicy,
    NoQuestionPolicy,
    OutcomeEntropyPolicy,
    QuestionPolicy,
    RandomQuestionPolicy,
    WidestImpactPolicy,
)


_ROUTE_COST = {
    NextAction.ASK_PATIENT: 1,
    NextAction.LOOKUP_RECORD: 2,
    NextAction.REQUEST_VERIFICATION: 3,
}

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

_TOPOLOGIES: dict[str, tuple[tuple[int, ...], ...]] = {
    "shared_hub": ((0,), (0, 1), (0, 2), (3,), (4,)),
    "chain": ((0, 1), (1, 2), (2, 3), (3, 4), (4, 0)),
    "gated_hub": ((0, 1), (0, 2), (0, 3), (4,), (1, 4)),
    "separated": ((0,), (1,), (2,), (3, 4), (2, 4)),
    "overlapping_pairs": ((0, 1), (0, 2), (1, 3), (2, 4), (3, 4)),
    "three_way": ((0, 1, 2), (0, 3), (1, 4), (2, 3), (2, 4)),
    "cost_conflict": ((0,), (0, 1), (0, 2), (3,), (4,)),
}

_DISEASE_LABELS = ("2형 당뇨병", "유방암", "주요우울장애")


@dataclass(frozen=True, slots=True)
class _Simulation:
    trial_recovery: float
    candidate_recovery: float
    confirmation_recovery: float
    unsafe_decisions: int
    actions: int
    route_cost: int
    selected_fact_ids: tuple[str, ...]
    selected_actions: tuple[str, ...]
    mismatched_trial_ids: tuple[str, ...]


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _evidence_source(route: NextAction) -> tuple[EvidenceSourceType, VerificationStatus]:
    return _SOURCE_BY_ROUTE[route]


def build_stress_case(
    topology: str,
    structure_number: int,
    *,
    seed: int,
) -> InteractiveCase:
    """Build one inspectable five-fact graph without clinical-performance claims."""

    if topology not in _TOPOLOGIES:
        raise ValueError(f"unknown topology: {topology}")
    rng = random.Random(f"{seed}:{topology}:{structure_number}")
    permutation = list(range(5))
    rng.shuffle(permutation)
    trial_links = tuple(
        tuple(permutation[index] for index in trial)
        for trial in _TOPOLOGIES[topology]
    )
    routes = [
        NextAction.ASK_PATIENT,
        NextAction.ASK_PATIENT,
        NextAction.LOOKUP_RECORD,
        NextAction.LOOKUP_RECORD,
        NextAction.REQUEST_VERIFICATION,
    ]
    rng.shuffle(routes)
    if topology == "cost_conflict":
        routes[permutation[0]] = NextAction.REQUEST_VERIFICATION

    case_id = f"stress-{topology}-{structure_number:04d}"
    as_of = datetime(2026, 8, 21, 12, tzinfo=timezone.utc)
    visible = EvidenceFact(
        evidence_id=f"{case_id}-diagnosis",
        statement="구조 실험용 합성 환자의 대상 질환이 확인되어 있다.",
        source_type=EvidenceSourceType.SYNTHETIC_CASE,
        source_location=f"synthetic-stress:{case_id}#diagnosis",
        event_date=date(2026, 8, 1),
        recorded_date=date(2026, 8, 1),
        verification_status=VerificationStatus.VERIFIED,
        concept="diagnosis_present",
        value=1,
        unit="bool",
    )
    hidden: list[InteractiveHiddenFact] = []
    full_facts = [visible]
    for fact_index, route in enumerate(routes):
        source_type, verification = _evidence_source(route)
        fact_id = f"{case_id}-fact-{fact_index}"
        evidence = EvidenceFact(
            evidence_id=f"{fact_id}-authored-answer",
            statement=f"구조 실험용 합성 정보 {fact_index + 1}: 조건 충족",
            source_type=source_type,
            source_location=f"synthetic-stress:{case_id}#fact-{fact_index}",
            event_date=date(2026, 8, 20),
            recorded_date=date(2026, 8, 20),
            verification_status=verification,
            concept=f"{case_id}-concept-{fact_index}",
            value=1,
            unit="bool",
        )
        full_facts.append(evidence)
        hidden.append(
            InteractiveHiddenFact(
                request=PublicFactRequest(
                    fact_id=fact_id,
                    description=f"구조 실험용 확인 정보 {fact_index + 1}",
                    available_actions=(route,),
                ),
                answer=HiddenFactAnswer(
                    fact_id=fact_id,
                    access_path=route,
                    evidence=evidence,
                ),
            )
        )
    rng.shuffle(hidden)

    trials = []
    for trial_index, fact_indices in enumerate(trial_links):
        trial_id = f"{case_id}-trial-{trial_index}"
        criteria = []
        for criterion_index, fact_index in enumerate(fact_indices):
            source_type, verification = _evidence_source(routes[fact_index])
            criteria.append(
                TrialCriterion(
                    criterion_id=f"{trial_id}-criterion-{criterion_index}",
                    trial_id=trial_id,
                    kind=CriterionKind.INCLUSION,
                    statement=f"합성 정보 {fact_index + 1}이 확인되어야 한다.",
                    source_location=(
                        f"synthetic-stress:{case_id}#trial-{trial_index}-"
                        f"criterion-{criterion_index}"
                    ),
                    numeric_constraint=NumericConstraint(
                        concept=f"{case_id}-concept-{fact_index}",
                        operator=ComparisonOperator.EQ,
                        threshold=1,
                        unit="bool",
                    ),
                    evidence_requirement=EvidenceRequirement(
                        max_age_days=30,
                        allowed_source_types=[source_type],
                        allowed_verification_statuses=[verification],
                    ),
                )
            )
        trials.append(InteractiveTrial(trial_id=trial_id, criteria=criteria))

    return InteractiveCase(
        case_id=case_id,
        disease_group=_DISEASE_LABELS[structure_number % len(_DISEASE_LABELS)],
        full_patient_state=PatientState(
            patient_id=f"synthetic-{case_id}",
            as_of=as_of,
            facts=full_facts,
        ),
        initial_visible_evidence_ids=[visible.evidence_id],
        trials=trials,
        hidden_facts=hidden,
        action_budget=3,
    )


def _joint_probability(
    scenario: PatientScenario,
    fact_ids: Sequence[str],
    p_latent_zero: Sequence[float],
    p_latent_one: Sequence[float],
    latent_one_probability: float,
) -> float:
    answer_by_fact = {item.fact_id: item.evidence for item in scenario.answers}

    def conditional(probabilities: Sequence[float]) -> float:
        result = 1.0
        for fact_id, probability in zip(fact_ids, probabilities, strict=True):
            satisfies = "satisfies-rule" in answer_by_fact[fact_id].statement
            result *= probability if satisfies else 1 - probability
        return result

    return (
        (1 - latent_one_probability) * conditional(p_latent_zero)
        + latent_one_probability * conditional(p_latent_one)
    )


def _reweight_distribution(
    base: ScenarioDistribution,
    p_latent_zero: Sequence[float],
    p_latent_one: Sequence[float],
    latent_one_probability: float,
) -> ScenarioDistribution:
    fact_ids = sorted(item.fact_id for item in base.scenarios[0].answers)
    weights = [
        _joint_probability(
            scenario,
            fact_ids,
            p_latent_zero,
            p_latent_one,
            latent_one_probability,
        )
        for scenario in base.scenarios
    ]
    total = sum(weights)
    return ScenarioDistribution(
        case_id=base.case_id,
        scenarios=[
            scenario.model_copy(update={"probability": weight / total})
            for scenario, weight in zip(base.scenarios, weights, strict=True)
        ],
    )


def build_stress_distributions(
    case: InteractiveCase,
    *,
    seed: int,
) -> tuple[ScenarioDistribution, ScenarioDistribution, ScenarioDistribution]:
    """Return development, similar held-out, and shifted joint distributions."""

    rng = random.Random(f"{seed}:{case.case_id}:distribution")
    base = build_uniform_binary_scenarios(case)
    centers = [rng.uniform(0.18, 0.82) for _ in range(5)]
    directions = [rng.choice((-1, 1)) for _ in range(5)]
    strengths = [rng.uniform(0.05, 0.22) for _ in range(5)]

    def latent_values(center_values: Sequence[float], reverse: bool = False):
        zero = []
        one = []
        for center, direction, strength in zip(
            center_values, directions, strengths, strict=True
        ):
            signed = -direction if reverse else direction
            zero.append(min(0.97, max(0.03, center - signed * strength)))
            one.append(min(0.97, max(0.03, center + signed * strength)))
        return zero, one

    development_zero, development_one = latent_values(centers)
    matched_centers = [
        min(0.9, max(0.1, center + rng.uniform(-0.07, 0.07)))
        for center in centers
    ]
    matched_zero, matched_one = latent_values(matched_centers)
    shifted_centers = [
        1 - center if index % 2 == 0 else min(0.9, max(0.1, center + 0.15))
        for index, center in enumerate(centers)
    ]
    shifted_zero, shifted_one = latent_values(shifted_centers, reverse=True)
    return (
        _reweight_distribution(
            base, development_zero, development_one, rng.uniform(0.35, 0.65)
        ),
        _reweight_distribution(
            base, matched_zero, matched_one, rng.uniform(0.35, 0.65)
        ),
        _reweight_distribution(
            base, shifted_zero, shifted_one, rng.uniform(0.65, 0.85)
        ),
    )


def _simulate(
    view: InteractivePolicyView,
    initial_state: PatientState,
    scenario: PatientScenario,
    policy: QuestionPolicy,
) -> _Simulation:
    answers = {item.fact_id: item.evidence for item in scenario.answers}
    state = initial_state
    snapshot = evaluate_policy_view(view, state)
    revealed: set[str] = set()
    route_cost = 0
    action_count = 0
    selected_fact_ids = []
    selected_actions = []
    for _ in range(view.action_budget):
        action: AgentAction = policy.select(view, snapshot, frozenset(revealed))
        if action.action in {NextAction.NONE, NextAction.DEFER}:
            break
        if action.target_fact_id is None or action.target_fact_id in revealed:
            raise ValueError("policy selected an invalid stress-benchmark fact")
        public = next(
            item
            for item in view.available_information
            if item.fact_id == action.target_fact_id
        )
        if action.action not in public.available_actions:
            raise ValueError("policy selected an unavailable stress-benchmark route")
        evidence = answers[action.target_fact_id]
        state = state.model_copy(update={"facts": [*state.facts, evidence]})
        revealed.add(action.target_fact_id)
        route_cost += _ROUTE_COST[action.action]
        action_count += 1
        selected_fact_ids.append(action.target_fact_id)
        selected_actions.append(action.action.value)
        snapshot = evaluate_policy_view(view, state)

    full = evaluate_policy_view(
        view,
        initial_state.model_copy(
            update={
                "facts": [
                    *initial_state.facts,
                    *(answers[item] for item in sorted(answers)),
                ]
            }
        ),
    )
    final_by_id = {item.trial_id: item for item in snapshot.decisions}
    full_by_id = {item.trial_id: item for item in full.decisions}
    trial_ids = sorted(full_by_id)
    status_matches = sum(
        (
            final_by_id[item].candidate_status,
            final_by_id[item].confirmation_status,
        )
        == (
            full_by_id[item].candidate_status,
            full_by_id[item].confirmation_status,
        )
        for item in trial_ids
    )
    mismatched_trial_ids = tuple(
        item
        for item in trial_ids
        if (
            final_by_id[item].candidate_status,
            final_by_id[item].confirmation_status,
        )
        != (
            full_by_id[item].candidate_status,
            full_by_id[item].confirmation_status,
        )
    )
    candidate_matches = sum(
        final_by_id[item].candidate_status is full_by_id[item].candidate_status
        for item in trial_ids
    )
    confirmation_matches = sum(
        final_by_id[item].confirmation_status
        is full_by_id[item].confirmation_status
        for item in trial_ids
    )
    unsafe = sum(
        (
            final_by_id[item].candidate_status is CandidateStatus.REMOVE
            and full_by_id[item].candidate_status is not CandidateStatus.REMOVE
        )
        or (
            final_by_id[item].confirmation_status is ConfirmationStatus.CONFIRMED
            and full_by_id[item].confirmation_status
            is not ConfirmationStatus.CONFIRMED
        )
        for item in trial_ids
    )
    return _Simulation(
        trial_recovery=status_matches / len(trial_ids),
        candidate_recovery=candidate_matches / len(trial_ids),
        confirmation_recovery=confirmation_matches / len(trial_ids),
        unsafe_decisions=unsafe,
        actions=action_count,
        route_cost=route_cost,
        selected_fact_ids=tuple(selected_fact_ids),
        selected_actions=tuple(selected_actions),
        mismatched_trial_ids=mismatched_trial_ids,
    )


def _weighted_metrics(
    simulations: Sequence[_Simulation],
    distribution: ScenarioDistribution,
) -> dict[str, float]:
    probabilities = [item.probability for item in distribution.scenarios]

    def expected(attribute: str) -> float:
        return sum(
            probability * getattr(simulation, attribute)
            for probability, simulation in zip(
                probabilities, simulations, strict=True
            )
        )

    return {
        "expected_trial_recovery": expected("trial_recovery"),
        "worst_trial_recovery": min(item.trial_recovery for item in simulations),
        "expected_candidate_recovery": expected("candidate_recovery"),
        "expected_confirmation_recovery": expected("confirmation_recovery"),
        "expected_unsafe_decisions": expected("unsafe_decisions"),
        "expected_actions": expected("actions"),
        "expected_route_cost": expected("route_cost"),
    }


def _policies(
    view: InteractivePolicyView,
    initial_state: PatientState,
    development: ScenarioDistribution,
    *,
    seed: int,
) -> list[QuestionPolicy]:
    return [
        NoQuestionPolicy(),
        RandomQuestionPolicy(seed),
        WidestImpactPolicy(),
        ImpactCostPolicy(),
        ClarifyTrialRulePolicy(),
        OutcomeEntropyPolicy(view, development),
        ExactDecisionTreePolicy(
            view,
            initial_state,
            development,
            ExactPolicyObjective.EXPECTED,
            planning_horizon=1,
        ),
        ExactDecisionTreePolicy(
            view,
            initial_state,
            development,
            ExactPolicyObjective.EXPECTED,
        ),
        ExactDecisionTreePolicy(
            view,
            initial_state,
            development,
            ExactPolicyObjective.WORST_CASE,
            planning_horizon=1,
        ),
        ExactDecisionTreePolicy(
            view,
            initial_state,
            development,
            ExactPolicyObjective.WORST_CASE,
        ),
    ]


def _aggregate_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(row["evaluation_distribution"], row["policy_id"])].append(row)
    result = []
    metric_names = (
        "expected_trial_recovery",
        "worst_trial_recovery",
        "expected_candidate_recovery",
        "expected_confirmation_recovery",
        "expected_unsafe_decisions",
        "expected_actions",
        "expected_route_cost",
    )
    for (distribution, policy_id), items in sorted(groups.items()):
        result.append(
            {
                "evaluation_distribution": distribution,
                "policy_id": policy_id,
                "structure_count": len(items),
                **{
                    metric: mean(item[metric] for item in items)
                    for metric in metric_names
                },
            }
        )
    return result


def _aggregate_topology_rows(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[
            (
                row["topology"],
                row["evaluation_distribution"],
                row["policy_id"],
            )
        ].append(row)
    result = []
    for (topology, distribution, policy_id), items in sorted(groups.items()):
        result.append(
            {
                "topology": topology,
                "evaluation_distribution": distribution,
                "policy_id": policy_id,
                "structure_count": len(items),
                "expected_trial_recovery": mean(
                    item["expected_trial_recovery"] for item in items
                ),
                "worst_trial_recovery": mean(
                    item["worst_trial_recovery"] for item in items
                ),
                "expected_actions": mean(item["expected_actions"] for item in items),
                "expected_route_cost": mean(
                    item["expected_route_cost"] for item in items
                ),
            }
        )
    return result


def _difference_summary(differences: Sequence[float]) -> dict[str, Any]:
    ordered = sorted(differences)
    return {
        "mean_recovery_difference": mean(differences),
        "fifth_percentile_difference": ordered[
            int(0.05 * (len(ordered) - 1))
        ],
        "ninety_fifth_percentile_difference": ordered[
            int(0.95 * (len(ordered) - 1))
        ],
        "wins": sum(item > 1e-12 for item in differences),
        "ties": sum(abs(item) <= 1e-12 for item in differences),
        "losses": sum(item < -1e-12 for item in differences),
    }


def _paired_comparisons(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    simple_ids = {
        "widest_impact",
        "impact_per_cost",
        "clarifytrial_rule_v1",
        "outcome_entropy",
    }
    aggregate = _aggregate_rows(rows)
    development_simple = max(
        (
            item
            for item in aggregate
            if item["evaluation_distribution"] == "development"
            and item["policy_id"] in simple_ids
        ),
        key=lambda item: item["expected_trial_recovery"],
    )
    baseline_id = development_simple["policy_id"]
    by_distribution: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_distribution[row["evaluation_distribution"]].append(row)
    comparisons = []
    for distribution, items in sorted(by_distribution.items()):
        if distribution == "development":
            continue
        by_key = {
            (item["structure_id"], item["policy_id"]): item for item in items
        }
        structure_ids = sorted({item["structure_id"] for item in items})
        for candidate_id in (
            "exact_decision_tree_expected_horizon_1_v1",
            "exact_decision_tree_expected_v1",
            "exact_decision_tree_worst_case_horizon_1_v1",
            "exact_decision_tree_worst_case_v1",
        ):
            differences = [
                by_key[(structure_id, candidate_id)]["expected_trial_recovery"]
                - by_key[(structure_id, baseline_id)]["expected_trial_recovery"]
                for structure_id in structure_ids
            ]
            comparisons.append(
                {
                    "evaluation_distribution": distribution,
                    "candidate_policy_id": candidate_id,
                    "strongest_simple_policy_id": baseline_id,
                    **_difference_summary(differences),
                }
            )
    return comparisons


def _horizon_comparisons(rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    by_key = {
        (
            item["evaluation_distribution"],
            item["structure_id"],
            item["policy_id"],
        ): item
        for item in rows
    }
    structure_ids = sorted({item["structure_id"] for item in rows})
    result = []
    for distribution in ("similar_heldout", "shifted_heldout"):
        for objective in ("expected", "worst_case"):
            full_id = f"exact_decision_tree_{objective}_v1"
            horizon_id = f"exact_decision_tree_{objective}_horizon_1_v1"
            differences = [
                by_key[(distribution, structure_id, full_id)][
                    "expected_trial_recovery"
                ]
                - by_key[(distribution, structure_id, horizon_id)][
                    "expected_trial_recovery"
                ]
                for structure_id in structure_ids
            ]
            result.append(
                {
                    "evaluation_distribution": distribution,
                    "candidate_policy_id": full_id,
                    "baseline_policy_id": horizon_id,
                    **_difference_summary(differences),
                }
            )
    return result


def run_interactive_stress(
    output_dir: str | Path,
    *,
    structures_per_topology: int = 100,
    seed: int = 20_260_821,
    progress=None,
) -> Path:
    """Run the large no-LLM structural benchmark and write replayable results."""

    if structures_per_topology <= 0:
        raise ValueError("structures_per_topology must be positive")
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    plan = {
        "protocol_id": "interactive-structural-stress-v1",
        "seed": seed,
        "topologies": sorted(_TOPOLOGIES),
        "structures_per_topology": structures_per_topology,
        "structure_count": structures_per_topology * len(_TOPOLOGIES),
        "facts_per_structure": 5,
        "trials_per_structure": 5,
        "action_budget": 3,
        "possible_patient_states_per_structure": 32,
        "evaluation_distributions": [
            "development",
            "similar_heldout",
            "shifted_heldout",
        ],
        "model_calls": 0,
        "scope": "질문 정책의 구조적 가치만 보는 합성 진단이며 임상 성능이 아니다.",
        "medical_disclaimer": DEFAULT_MEDICAL_DISCLAIMER,
    }
    _write_json(destination / "plan.json", plan)

    rows = []
    completed = 0
    for topology in sorted(_TOPOLOGIES):
        for structure_number in range(structures_per_topology):
            case = build_stress_case(topology, structure_number, seed=seed)
            view = case.public_policy_view()
            initial_state = case.initial_patient_state()
            development, matched, shifted = build_stress_distributions(
                case, seed=seed
            )
            policies = _policies(
                view,
                initial_state,
                development,
                seed=seed + structure_number,
            )
            for policy in policies:
                simulations = [
                    _simulate(view, initial_state, scenario, policy)
                    for scenario in development.scenarios
                ]
                for distribution_name, distribution in (
                    ("development", development),
                    ("similar_heldout", matched),
                    ("shifted_heldout", shifted),
                ):
                    rows.append(
                        {
                            "structure_id": case.case_id,
                            "topology": topology,
                            "evaluation_distribution": distribution_name,
                            "policy_id": policy.policy_id,
                            **_weighted_metrics(simulations, distribution),
                        }
                    )
            completed += 1
            if progress is not None and completed % max(
                1, structures_per_topology
            ) == 0:
                progress(
                    f"completed {completed}/{plan['structure_count']} structures"
                )

    rows_path = destination / "structure-results.jsonl"
    rows_path.write_text(
        "".join(
            json.dumps(item, ensure_ascii=False) + "\n" for item in rows
        ),
        encoding="utf-8",
    )
    summary = {
        **plan,
        "policy_metrics": _aggregate_rows(rows),
        "topology_metrics": _aggregate_topology_rows(rows),
        "paired_comparisons": _paired_comparisons(rows),
        "horizon_comparisons": _horizon_comparisons(rows),
        "result_status": "structural_diagnostic_only",
    }
    summary_path = destination / "summary.json"
    _write_json(summary_path, summary)
    return summary_path
