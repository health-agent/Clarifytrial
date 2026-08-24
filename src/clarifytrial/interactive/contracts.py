"""Contracts for the multi-trial clarification benchmark.

The full synthetic patient state is an evaluation asset.  Policies receive
only the initial state, public request descriptions, and facts released by an
executed action.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from ..concepts import concepts_equivalent, normalized_concept
from ..contracts import (
    ConfirmationStatus,
    ContractModel,
    EvidenceFact,
    NextAction,
    PatientState,
    TrialCriterion,
    TrialDecision,
)
from ..environment import HiddenFactAnswer, PublicFactRequest, ToolExecutionResult
from ..llm.base import ModelUsage


class InteractiveTrial(ContractModel):
    """One fixed candidate trial in an interactive case."""

    trial_id: str = Field(min_length=1)
    criteria: list[TrialCriterion] = Field(min_length=1)

    @model_validator(mode="after")
    def criterion_references_are_consistent(self) -> "InteractiveTrial":
        criterion_ids = [item.criterion_id for item in self.criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("criteria must not repeat criterion_id")
        if any(item.trial_id != self.trial_id for item in self.criteria):
            raise ValueError("every criterion must belong to trial_id")
        if not any(item.required for item in self.criteria):
            raise ValueError("at least one criterion must be required")
        return self


class InteractiveHiddenFact(ContractModel):
    """A public request paired with its private synthetic answer."""

    request: PublicFactRequest
    answer: HiddenFactAnswer

    @model_validator(mode="after")
    def public_and_private_sides_match(self) -> "InteractiveHiddenFact":
        if self.request.fact_id != self.answer.fact_id:
            raise ValueError("request and answer must use the same fact_id")
        if self.answer.access_path not in self.request.available_actions:
            raise ValueError("answer path must be publicly available")
        return self


class InteractivePublicFact(ContractModel):
    """The answer-free information menu exposed to a question policy."""

    fact_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    available_actions: list[NextAction] = Field(min_length=1)
    related_criterion_ids: list[str] = Field(min_length=1)


class InteractivePolicyView(ContractModel):
    """Public case metadata from which a policy must choose an action."""

    case_id: str = Field(min_length=1)
    disease_group: str = Field(min_length=1)
    trials: list[InteractiveTrial] = Field(min_length=1)
    available_information: list[InteractivePublicFact] = Field(default_factory=list)
    action_budget: int = Field(ge=0)


class ScenarioFactAnswer(ContractModel):
    """One possible answer value used only by the planning model."""

    fact_id: str = Field(min_length=1)
    evidence: EvidenceFact


class PatientScenario(ContractModel):
    """One possible complete hidden state and its planning probability."""

    scenario_id: str = Field(min_length=1)
    probability: float = Field(gt=0, le=1)
    answers: list[ScenarioFactAnswer] = Field(min_length=1)

    @model_validator(mode="after")
    def answers_are_unique(self) -> "PatientScenario":
        fact_ids = [item.fact_id for item in self.answers]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("scenario answers must not repeat fact_id")
        return self


class ScenarioDistribution(ContractModel):
    """Answer possibilities known to the planner without identifying reality."""

    case_id: str = Field(min_length=1)
    scenarios: list[PatientScenario] = Field(min_length=1)

    @model_validator(mode="after")
    def distribution_is_complete(self) -> "ScenarioDistribution":
        scenario_ids = [item.scenario_id for item in self.scenarios]
        if len(scenario_ids) != len(set(scenario_ids)):
            raise ValueError("scenarios must not repeat scenario_id")
        fact_sets = [{item.fact_id for item in row.answers} for row in self.scenarios]
        if any(item != fact_sets[0] for item in fact_sets[1:]):
            raise ValueError("every scenario must define the same facts")
        if abs(sum(item.probability for item in self.scenarios) - 1.0) > 1e-9:
            raise ValueError("scenario probabilities must sum to one")
        return self


class ExactPolicyObjective(StrEnum):
    """How outcomes from the complete question tree are combined."""

    EXPECTED = "expected"
    WORST_CASE = "worst_case"


class ExactPolicyValue(ContractModel):
    """Result of an exact question tree under its declared objective."""

    objective: ExactPolicyObjective
    unsafe_trial_decisions: float = Field(ge=0)
    average_trial_status_recovery: float = Field(ge=0, le=1)
    worst_case_trial_status_recovery: float = Field(ge=0, le=1)
    action_count: float = Field(ge=0)
    route_cost: float = Field(ge=0)


class ExactPolicyChoice(ContractModel):
    """The first action of an exhaustively evaluated question tree."""

    target_fact_id: str | None = None
    value: ExactPolicyValue
    evaluated_states: int = Field(ge=1)
    belief_scenario_count: int = Field(ge=1)
    remaining_action_budget: int = Field(ge=0)


class InteractiveCase(ContractModel):
    """One synthetic patient, five fixed candidates, and hidden information."""

    case_id: str = Field(min_length=1)
    disease_group: str = Field(min_length=1)
    full_patient_state: PatientState
    initial_visible_evidence_ids: list[str] = Field(min_length=1)
    trials: list[InteractiveTrial] = Field(min_length=1)
    hidden_facts: list[InteractiveHiddenFact] = Field(min_length=1)
    action_budget: int = Field(default=3, ge=0)

    @model_validator(mode="after")
    def case_is_a_closed_synthetic_world(self) -> "InteractiveCase":
        evidence_by_id = {
            item.evidence_id: item for item in self.full_patient_state.facts
        }
        if len(evidence_by_id) != len(self.full_patient_state.facts):
            raise ValueError("full patient facts must have unique evidence_id")
        if len(self.initial_visible_evidence_ids) != len(
            set(self.initial_visible_evidence_ids)
        ):
            raise ValueError("initial_visible_evidence_ids must be unique")
        unknown_visible = set(self.initial_visible_evidence_ids) - set(evidence_by_id)
        if unknown_visible:
            raise ValueError("initial visible facts must exist in the full state")

        trial_ids = [trial.trial_id for trial in self.trials]
        if len(trial_ids) != len(set(trial_ids)):
            raise ValueError("trials must not repeat trial_id")
        criterion_ids = [
            criterion.criterion_id
            for trial in self.trials
            for criterion in trial.criteria
        ]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("criterion_id must be unique across the case")
        known_criteria = set(criterion_ids)

        fact_ids = [item.request.fact_id for item in self.hidden_facts]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("hidden facts must not repeat fact_id")
        hidden_evidence_ids: set[str] = set()
        used_concepts = {
            normalized_concept(criterion.numeric_constraint.concept)
            for trial in self.trials
            for criterion in trial.criteria
            if criterion.numeric_constraint is not None
        }
        for item in self.hidden_facts:
            answer = item.answer.evidence
            authored = evidence_by_id.get(answer.evidence_id)
            if authored is None or authored != answer:
                raise ValueError("every hidden answer must be copied from the full state")
            if answer.evidence_id in self.initial_visible_evidence_ids:
                raise ValueError("hidden evidence must not be initially visible")
            if answer.evidence_id in hidden_evidence_ids:
                raise ValueError("hidden answers must not repeat evidence_id")
            hidden_evidence_ids.add(answer.evidence_id)
            if (
                answer.concept is None
                or normalized_concept(answer.concept) not in used_concepts
            ):
                raise ValueError("every hidden fact must affect a trial criterion")
            if not item.request.description.strip():
                raise ValueError("hidden facts need a public description")

        partition = set(self.initial_visible_evidence_ids) | hidden_evidence_ids
        if partition != set(evidence_by_id):
            raise ValueError("initial and hidden evidence must partition the full state")
        return self

    def initial_patient_state(self) -> PatientState:
        visible = set(self.initial_visible_evidence_ids)
        return self.full_patient_state.model_copy(
            update={
                "facts": [
                    item
                    for item in self.full_patient_state.facts
                    if item.evidence_id in visible
                ]
            }
        )

    def public_policy_view(self) -> InteractivePolicyView:
        """Build the question-policy input without private answer values."""

        facts = []
        for hidden in self.hidden_facts:
            concept = hidden.answer.evidence.concept
            related = [
                criterion.criterion_id
                for trial in self.trials
                for criterion in trial.criteria
                if criterion.numeric_constraint is not None
                and concepts_equivalent(
                    criterion.numeric_constraint.concept,
                    concept,
                )
            ]
            facts.append(
                InteractivePublicFact(
                    fact_id=hidden.request.fact_id,
                    description=hidden.request.description,
                    available_actions=list(hidden.request.available_actions),
                    related_criterion_ids=related,
                )
            )
        return InteractivePolicyView(
            case_id=self.case_id,
            disease_group=self.disease_group,
            trials=self.trials,
            available_information=facts,
            action_budget=self.action_budget,
        )


class InteractiveSnapshot(ContractModel):
    """All candidate decisions at one visible patient state."""

    patient_state: PatientState
    decisions: list[TrialDecision]

    @model_validator(mode="after")
    def decisions_are_unique(self) -> "InteractiveSnapshot":
        trial_ids = [item.trial_id for item in self.decisions]
        if len(trial_ids) != len(set(trial_ids)):
            raise ValueError("snapshot decisions must not repeat trial_id")
        return self

    @property
    def resolved_trial_count(self) -> int:
        return sum(
            item.confirmation_status
            in {ConfirmationStatus.CONFIRMED, ConfirmationStatus.INELIGIBLE}
            for item in self.decisions
        )


class MinimalQuestionGold(ContractModel):
    """All smallest fact sets that reproduce the full-information decisions."""

    minimal_fact_sets: list[list[str]]
    recoverable_within_budget: bool


class FactSensitivity(ContractModel):
    """How much one hidden fact changes recovery across all fact combinations."""

    fact_id: str = Field(min_length=1)
    public_related_trial_count: int = Field(ge=0)
    standalone_recovery_gain: float
    leave_one_out_recovery_loss: float
    average_marginal_recovery: float


class SensitivityProfile(ContractModel):
    """Exact sensitivity analysis over the small closed synthetic case."""

    fact_count: int = Field(ge=1)
    evaluated_state_count: int = Field(ge=1)
    facts: list[FactSensitivity] = Field(min_length=1)


class InteractiveActionRecord(ContractModel):
    """One selected information action and the visible state after execution."""

    step: int = Field(ge=1)
    action: NextAction
    target_fact_id: str | None = None
    result: ToolExecutionResult


class InteractiveRunMetrics(ContractModel):
    """Per-case measures used by every clarification policy."""

    trial_status_recovery: float = Field(ge=0, le=1)
    candidate_status_recovery: float = Field(ge=0, le=1)
    confirmation_status_recovery: float = Field(ge=0, le=1)
    necessary_fact_recall: float = Field(ge=0, le=1)
    unnecessary_action_count: int = Field(ge=0)
    false_candidate_removals: int = Field(ge=0)
    missed_exclusions: int = Field(ge=0)
    premature_confirmations: int = Field(ge=0)
    unresolved_to_resolved: int = Field(ge=0)
    action_count: int = Field(ge=0)
    realized_impact_capture: float = Field(ge=0, le=1)


class InteractivePolicyRun(ContractModel):
    """One policy's complete, replayable result for one case."""

    case_id: str
    disease_group: str
    policy_id: str
    initial_snapshot: InteractiveSnapshot
    final_snapshot: InteractiveSnapshot
    full_information_snapshot: InteractiveSnapshot
    question_gold: MinimalQuestionGold
    sensitivity: SensitivityProfile
    action_history: list[InteractiveActionRecord]
    usage: list[ModelUsage] = Field(default_factory=list)
    metrics: InteractiveRunMetrics


class InteractiveBenchmarkSummary(ContractModel):
    """Aggregate results for one policy over a fixed case collection."""

    policy_id: str
    case_count: int = Field(ge=1)
    mean_trial_status_recovery: float = Field(ge=0, le=1)
    mean_candidate_status_recovery: float = Field(ge=0, le=1)
    mean_confirmation_status_recovery: float = Field(ge=0, le=1)
    mean_necessary_fact_recall: float = Field(ge=0, le=1)
    mean_realized_impact_capture: float = Field(ge=0, le=1)
    total_unnecessary_actions: int = Field(ge=0)
    total_false_candidate_removals: int = Field(ge=0)
    total_missed_exclusions: int = Field(ge=0)
    total_premature_confirmations: int = Field(ge=0)
    total_actions: int = Field(ge=0)
    total_input_tokens: int | None = Field(default=None, ge=0)
    total_output_tokens: int | None = Field(default=None, ge=0)
    total_reasoning_tokens: int | None = Field(default=None, ge=0)
    total_tokens: int | None = Field(default=None, ge=0)
