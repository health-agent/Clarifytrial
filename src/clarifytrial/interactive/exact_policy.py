"""Exhaustive adaptive question planning for small clinical-trial fact graphs."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from collections.abc import Mapping
from typing import Sequence

from ..contracts import (
    AgentAction,
    CandidateStatus,
    ComparisonOperator,
    ConfirmationStatus,
    EvidenceFact,
    EvidenceSourceType,
    NextAction,
    PatientState,
    VerificationStatus,
)
from ..llm.base import ModelUsage
from ..mechanical_checks import evaluate_criterion
from .contracts import (
    ExactPolicyChoice,
    ExactPolicyObjective,
    ExactPolicyValue,
    InteractiveCase,
    InteractivePolicyView,
    InteractiveSnapshot,
    PatientScenario,
    ScenarioDistribution,
    ScenarioFactAnswer,
)
from .oracle import evaluate_policy_view


_ROUTE_COST = {
    NextAction.ASK_PATIENT: 1,
    NextAction.LOOKUP_RECORD: 2,
    NextAction.REQUEST_VERIFICATION: 3,
}

_PLANNING_SOURCE_BY_ROUTE = {
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


def _crossing_values(
    operator: ComparisonOperator,
    threshold: float,
    unit: str,
) -> tuple[float, float]:
    """Return one satisfying and one violating value for a numeric rule."""

    if operator is ComparisonOperator.EQ:
        alternative = 1.0 - threshold if unit == "bool" and threshold in {0, 1} else threshold + 1
        return threshold, alternative
    margin = max(0.1, abs(threshold) * 0.1)
    if operator in {ComparisonOperator.GT, ComparisonOperator.GTE}:
        return threshold + margin, threshold - margin
    return threshold - margin, threshold + margin


def build_binary_scenarios(
    case: InteractiveCase,
    satisfying_probability_by_fact: Mapping[str, float] | None = None,
) -> ScenarioDistribution:
    """Build every pass/fail combination without marking the actual patient.

    Probabilities describe the planning population, not the answer of this
    patient.  They must be fixed from development data or a declared synthetic
    generator before an evaluation case is opened.
    """

    view = case.public_policy_view()
    criterion_by_id = {
        criterion.criterion_id: criterion
        for trial in view.trials
        for criterion in trial.criteria
    }
    outcomes: dict[str, tuple[EvidenceFact, EvidenceFact]] = {}
    for public in view.available_information:
        rules = [
            criterion_by_id[item].numeric_constraint
            for item in public.related_criterion_ids
        ]
        if not rules or any(item is None for item in rules):
            raise ValueError("binary scenarios require numeric rules")
        first = rules[0]
        assert first is not None
        if any(item != first for item in rules[1:]):
            raise ValueError("one fact must use the same rule in every linked criterion")
        satisfying, violating = _crossing_values(
            first.operator, first.threshold, first.unit
        )
        route = public.available_actions[0]
        if route not in _PLANNING_SOURCE_BY_ROUTE:
            raise ValueError("binary scenarios require an information-gathering route")
        source_type, verification = _PLANNING_SOURCE_BY_ROUTE[route]
        event_date = case.initial_patient_state().as_of.date()

        def planning_evidence(label: str, value: float) -> EvidenceFact:
            return EvidenceFact(
                evidence_id=f"planning-{case.case_id}-{public.fact_id}-{label}",
                statement=f"planning outcome: {first.concept} {label}",
                source_type=source_type,
                source_location=(
                    f"planning-scenario:{case.case_id}#{public.fact_id}-{label}"
                ),
                event_date=event_date,
                recorded_date=event_date,
                verification_status=verification,
                concept=first.concept,
                value=value,
                unit=first.unit,
            )

        outcomes[public.fact_id] = (
            planning_evidence("satisfies-rule", satisfying),
            planning_evidence("violates-rule", violating),
        )

    fact_ids = sorted(outcomes)
    probabilities = {
        fact_id: (
            0.5
            if satisfying_probability_by_fact is None
            else satisfying_probability_by_fact.get(fact_id, 0.5)
        )
        for fact_id in fact_ids
    }
    if any(value <= 0 or value >= 1 for value in probabilities.values()):
        raise ValueError("satisfying probabilities must be strictly between zero and one")
    if satisfying_probability_by_fact is not None:
        unknown = set(satisfying_probability_by_fact) - set(fact_ids)
        if unknown:
            raise ValueError("probabilities refer to unknown fact_id values")
    combinations = list(product((0, 1), repeat=len(fact_ids)))
    scenarios = []
    for position, choices in enumerate(combinations):
        probability = 1.0
        for fact_id, choice in zip(fact_ids, choices, strict=True):
            pass_probability = probabilities[fact_id]
            probability *= pass_probability if choice == 0 else 1 - pass_probability
        scenarios.append(
            PatientScenario(
                scenario_id=f"{case.case_id}-scenario-{position:03d}",
                probability=probability,
                answers=[
                    ScenarioFactAnswer(
                        fact_id=fact_id,
                        evidence=outcomes[fact_id][choice],
                    )
                    for fact_id, choice in zip(fact_ids, choices, strict=True)
                ],
            )
        )
    return ScenarioDistribution(case_id=case.case_id, scenarios=scenarios)


def build_uniform_binary_scenarios(case: InteractiveCase) -> ScenarioDistribution:
    """Build all binary outcomes with equal probabilities as a neutral baseline."""

    return build_binary_scenarios(case)


@dataclass(frozen=True, slots=True)
class _Value:
    unsafe: float
    average_recovery: float
    worst_recovery: float
    actions: float
    route_cost: float

    def key(
        self, objective: ExactPolicyObjective
    ) -> tuple[float, float, float, float, float]:
        primary_recovery, secondary_recovery = (
            (self.average_recovery, self.worst_recovery)
            if objective is ExactPolicyObjective.EXPECTED
            else (self.worst_recovery, self.average_recovery)
        )
        return (
            -self.unsafe,
            primary_recovery,
            secondary_recovery,
            -self.actions,
            -self.route_cost,
        )

    def public(self, objective: ExactPolicyObjective) -> ExactPolicyValue:
        return ExactPolicyValue(
            objective=objective,
            unsafe_trial_decisions=self.unsafe,
            average_trial_status_recovery=self.average_recovery,
            worst_case_trial_status_recovery=self.worst_recovery,
            action_count=self.actions,
            route_cost=self.route_cost,
        )


@dataclass(frozen=True, slots=True)
class _Plan:
    fact_id: str | None
    value: _Value


class _ExactSolver:
    def __init__(
        self,
        view: InteractivePolicyView,
        initial_state: PatientState,
        distribution: ScenarioDistribution,
        objective: ExactPolicyObjective,
    ) -> None:
        if view.case_id != distribution.case_id:
            raise ValueError("view and scenario distribution must share case_id")
        self.view = view
        self.initial_state = initial_state
        self.distribution = distribution
        self.objective = objective
        self.scenarios = tuple(distribution.scenarios)
        self.answer_by_scenario = tuple(
            {answer.fact_id: answer.evidence for answer in item.answers}
            for item in self.scenarios
        )
        self.all_scenarios_mask = (1 << len(self.scenarios)) - 1
        self._probability_cache: dict[int, float] = {0: 0.0}
        first_probability = self.scenarios[0].probability
        self._uniform_probability = (
            first_probability
            if all(
                abs(item.probability - first_probability) <= 1e-15
                for item in self.scenarios
            )
            else None
        )
        public_ids = {item.fact_id for item in view.available_information}
        scenario_ids = set(self.answer_by_scenario[0])
        if public_ids != scenario_ids:
            raise ValueError("scenario facts must match the public information menu")
        self.public_by_id = {item.fact_id: item for item in view.available_information}
        self.criterion_by_id = {
            criterion.criterion_id: criterion
            for trial in view.trials
            for criterion in trial.criteria
        }
        self.full_decisions = tuple(
            evaluate_policy_view(
                view, self._state_for_scenario(scenario, frozenset(public_ids))
            )
            for scenario in self.scenarios
        )
        self.outcome_masks: dict[str, dict[str, int]] = {
            fact_id: {} for fact_id in public_ids
        }
        self.full_status_masks: dict[tuple[str, str, str], int] = {}
        for index, answers in enumerate(self.answer_by_scenario):
            bit = 1 << index
            for fact_id, evidence in answers.items():
                outcome = self._fact_outcome_key(fact_id, evidence)
                self.outcome_masks[fact_id][outcome] = (
                    self.outcome_masks[fact_id].get(outcome, 0) | bit
                )
            for decision in self.full_decisions[index].decisions:
                status_key = (
                    decision.trial_id,
                    decision.candidate_status.value,
                    decision.confirmation_status.value,
                )
                self.full_status_masks[status_key] = (
                    self.full_status_masks.get(status_key, 0) | bit
                )
        self.memo: dict[
            tuple[int, tuple[tuple[str, str], ...], int], _Plan
        ] = {}
        self.evaluated_states = 0

    def solve(
        self,
        current_state: PatientState,
        revealed_fact_ids: frozenset[str],
        remaining_budget: int,
    ) -> ExactPolicyChoice:
        observations = self._observations(current_state, revealed_fact_ids)
        belief = self.all_scenarios_mask
        for fact_id, outcome in observations.items():
            belief &= self.outcome_masks[fact_id].get(outcome, 0)
        if belief == 0:
            raise ValueError("observed answers are outside the planning scenarios")
        plan = self._solve_state(belief, tuple(sorted(observations.items())), remaining_budget)
        return ExactPolicyChoice(
            target_fact_id=plan.fact_id,
            value=plan.value.public(self.objective),
            evaluated_states=max(1, self.evaluated_states),
            belief_scenario_count=belief.bit_count(),
            remaining_action_budget=remaining_budget,
        )

    def _solve_state(
        self,
        belief: int,
        observations: tuple[tuple[str, str], ...],
        remaining_budget: int,
    ) -> _Plan:
        key = (belief, observations, remaining_budget)
        cached = self.memo.get(key)
        if cached is not None:
            return cached
        self.evaluated_states += 1
        stop = _Plan(None, self._terminal_value(belief, observations))
        best = stop
        if remaining_budget > 0:
            observed_ids = {fact_id for fact_id, _ in observations}
            for fact_id in sorted(set(self.public_by_id) - observed_ids):
                branches = self._partition(belief, fact_id)
                branch_values: list[tuple[float, _Value]] = []
                belief_probability = self._mask_probability(belief)
                for outcome, scenario_mask in branches.items():
                    branch_weight = (
                        self._mask_probability(scenario_mask) / belief_probability
                        if self.objective is ExactPolicyObjective.EXPECTED
                        else scenario_mask.bit_count() / belief.bit_count()
                    )
                    child_observations = tuple(
                        sorted((*observations, (fact_id, outcome)))
                    )
                    child = self._solve_state(
                        scenario_mask, child_observations, remaining_budget - 1
                    )
                    branch_values.append((branch_weight, child.value))
                route = self.public_by_id[fact_id].available_actions[0]
                if self.objective is ExactPolicyObjective.EXPECTED:
                    value = _Value(
                        unsafe=sum(
                            weight * item.unsafe for weight, item in branch_values
                        ),
                        average_recovery=sum(
                            weight * item.average_recovery
                            for weight, item in branch_values
                        ),
                        worst_recovery=min(
                            item.worst_recovery for _, item in branch_values
                        ),
                        actions=1
                        + sum(weight * item.actions for weight, item in branch_values),
                        route_cost=_ROUTE_COST[route]
                        + sum(
                            weight * item.route_cost for weight, item in branch_values
                        ),
                    )
                else:
                    branch_results = [item for _, item in branch_values]
                    value = _Value(
                        unsafe=max(item.unsafe for item in branch_results),
                        average_recovery=sum(
                            weight * item.average_recovery
                            for weight, item in branch_values
                        ),
                        worst_recovery=min(
                            item.worst_recovery for item in branch_results
                        ),
                        actions=1 + max(item.actions for item in branch_results),
                        route_cost=_ROUTE_COST[route]
                        + max(item.route_cost for item in branch_results),
                )
                candidate = _Plan(fact_id, value)
                if candidate.value.key(self.objective) > best.value.key(self.objective):
                    best = candidate
        self.memo[key] = best
        return best

    def _terminal_value(
        self,
        belief: int,
        observations: tuple[tuple[str, str], ...],
    ) -> _Value:
        current = evaluate_policy_view(
            self.view,
            self._state_from_observations(
                observations, (belief & -belief).bit_length() - 1
            ),
        )
        trial_ids = [item.trial_id for item in current.decisions]
        matching_masks = []
        unsafe_masks = []
        for current_decision in current.decisions:
            matching_mask = self.full_status_masks.get(
                (
                    current_decision.trial_id,
                    current_decision.candidate_status.value,
                    current_decision.confirmation_status.value,
                ),
                0,
            )
            matching_masks.append(matching_mask)
            unsafe_mask = 0
            if current_decision.candidate_status is CandidateStatus.REMOVE:
                for (trial_id, candidate, _), status_mask in self.full_status_masks.items():
                    if trial_id == current_decision.trial_id and candidate != CandidateStatus.REMOVE.value:
                        unsafe_mask |= status_mask
            if current_decision.confirmation_status is ConfirmationStatus.CONFIRMED:
                for (trial_id, _, confirmation), status_mask in self.full_status_masks.items():
                    if trial_id == current_decision.trial_id and confirmation != ConfirmationStatus.CONFIRMED.value:
                        unsafe_mask |= status_mask
            unsafe_masks.append(unsafe_mask)
        if self.objective is ExactPolicyObjective.WORST_CASE:
            worst_recovery = self._extreme_count(
                matching_masks, belief, find_max=False
            ) / len(trial_ids)
            unsafe = float(self._extreme_count(unsafe_masks, belief, find_max=True))
        else:
            worst_recovery = self._extreme_count(
                matching_masks, belief, find_max=False
            ) / len(trial_ids)
        if self.objective is ExactPolicyObjective.EXPECTED:
            total_probability = self._mask_probability(belief)
            average_recovery = sum(
                self._mask_probability(belief & item)
                / (total_probability * len(trial_ids))
                for item in matching_masks
            )
            unsafe = sum(
                self._mask_probability(belief & item) / total_probability
                for item in unsafe_masks
            )
        else:
            average_recovery = sum(
                (belief & item).bit_count()
                / (belief.bit_count() * len(trial_ids))
                for item in matching_masks
            )
        return _Value(
            unsafe=unsafe,
            average_recovery=average_recovery,
            worst_recovery=worst_recovery,
            actions=0,
            route_cost=0,
        )

    @staticmethod
    def _extreme_count(
        event_masks: Sequence[int], belief: int, *, find_max: bool
    ) -> int:
        count_masks = [belief]
        for event_mask in event_masks:
            next_masks = [0] * (len(count_masks) + 1)
            for count, state_mask in enumerate(count_masks):
                next_masks[count] |= state_mask & ~event_mask
                next_masks[count + 1] |= state_mask & event_mask
            count_masks = next_masks
        indices = (
            range(len(count_masks) - 1, -1, -1)
            if find_max
            else range(len(count_masks))
        )
        return next(index for index in indices if count_masks[index])

    def _observations(
        self,
        state: PatientState,
        revealed_fact_ids: frozenset[str],
    ) -> dict[str, str]:
        evidence_by_id = {item.evidence_id: item for item in state.facts}
        result = {}
        for fact_id in revealed_fact_ids:
            public = self.public_by_id[fact_id]
            criterion = self.criterion_by_id[public.related_criterion_ids[0]]
            constraint = criterion.numeric_constraint
            if constraint is None:
                raise ValueError("exact policy observations require a numeric rule")
            observed = next(
                (
                    item
                    for item in reversed(tuple(evidence_by_id.values()))
                    if item.concept == constraint.concept
                ),
                None,
            )
            if observed is None:
                raise ValueError("revealed fact is absent from the patient state")
            result[fact_id] = self._fact_outcome_key(fact_id, observed)
        return result

    def _partition(
        self, belief: int, fact_id: str
    ) -> dict[str, int]:
        return {
            outcome: branch_mask
            for outcome, outcome_mask in self.outcome_masks[fact_id].items()
            if (branch_mask := belief & outcome_mask)
        }

    def _mask_probability(self, mask: int) -> float:
        cached = self._probability_cache.get(mask)
        if cached is not None:
            return cached
        if self._uniform_probability is not None:
            result = mask.bit_count() * self._uniform_probability
        else:
            result = 0.0
            remaining = mask
            while remaining:
                least = remaining & -remaining
                index = least.bit_length() - 1
                result += self.scenarios[index].probability
                remaining ^= least
        self._probability_cache[mask] = result
        return result

    def _fact_outcome_key(self, fact_id: str, evidence: EvidenceFact) -> str:
        public = self.public_by_id[fact_id]
        criterion = self.criterion_by_id[public.related_criterion_ids[0]]
        result = evaluate_criterion(
            criterion,
            self.initial_state.model_copy(
                update={"facts": [*self.initial_state.facts, evidence]}
            ),
        )
        return f"{result.clinical_status.value}:{result.evidence_sufficiency.value}"

    def _state_from_observations(
        self,
        observations: tuple[tuple[str, str], ...],
        representative_scenario_index: int,
    ) -> PatientState:
        answers = self.answer_by_scenario[representative_scenario_index]
        evidence = [answers[fact_id] for fact_id, _ in observations]
        return self.initial_state.model_copy(
            update={"facts": [*self.initial_state.facts, *evidence]}
        )

    def _state_for_scenario(
        self,
        scenario: PatientScenario,
        fact_ids: frozenset[str],
    ) -> PatientState:
        answers = {item.fact_id: item.evidence for item in scenario.answers}
        return self.initial_state.model_copy(
            update={
                "facts": [
                    *self.initial_state.facts,
                    *(answers[item] for item in sorted(fact_ids)),
                ]
            }
        )


class ExactDecisionTreePolicy:
    """Choose each action from the globally best bounded question tree."""

    def __init__(
        self,
        view: InteractivePolicyView,
        initial_state: PatientState,
        distribution: ScenarioDistribution,
        objective: ExactPolicyObjective,
    ) -> None:
        self._view = view
        self._objective = objective
        self._solver = _ExactSolver(view, initial_state, distribution, objective)
        self._choices: list[ExactPolicyChoice] = []

    @property
    def policy_id(self) -> str:
        return f"exact_decision_tree_{self._objective.value}_v1"

    @property
    def usage(self) -> Sequence[ModelUsage]:
        return ()

    @property
    def choices(self) -> tuple[ExactPolicyChoice, ...]:
        return tuple(self._choices)

    def select(
        self,
        view: InteractivePolicyView,
        snapshot: InteractiveSnapshot,
        revealed_fact_ids: frozenset[str],
    ) -> AgentAction:
        if view != self._view:
            raise ValueError("exact policy cannot be reused for another case")
        choice = self._solver.solve(
            snapshot.patient_state,
            revealed_fact_ids,
            view.action_budget - len(revealed_fact_ids),
        )
        self._choices.append(choice)
        if choice.target_fact_id is None:
            return AgentAction(
                action=NextAction.NONE,
                reason="더 확인해도 예상 후보 결과가 개선되지 않는다.",
            )
        public = next(
            item
            for item in view.available_information
            if item.fact_id == choice.target_fact_id
        )
        action = public.available_actions[0]
        message = (
            public.description
            if action in {NextAction.ASK_PATIENT, NextAction.REQUEST_VERIFICATION}
            else None
        )
        return AgentAction(
            action=action,
            target_fact_id=public.fact_id,
            related_criterion_ids=public.related_criterion_ids,
            reason="남은 행동 한도의 모든 질문·답변 가지를 계산했을 때 가장 안전하게 후보 결과를 회복한다.",
            message=message,
        )
