"""Deterministic full-information oracle for interactive synthetic cases."""

from __future__ import annotations

from itertools import combinations
from math import factorial

from ..contracts import (
    AgentAction,
    CriterionAssessment,
    NextAction,
    PatientState,
)
from ..decision_rules import aggregate_trial_decision
from ..mechanical_checks import evaluate_criterion
from .contracts import (
    FactSensitivity,
    InteractiveCase,
    InteractiveSnapshot,
    MinimalQuestionGold,
    SensitivityProfile,
)


def evaluate_interactive_case(
    case: InteractiveCase,
    patient_state: PatientState,
) -> InteractiveSnapshot:
    """Evaluate every fixed candidate using only visible structured evidence."""

    request_by_criterion: dict[str, list[str]] = {}
    for hidden in case.hidden_facts:
        for trial in case.trials:
            for criterion in trial.criteria:
                constraint = criterion.numeric_constraint
                if constraint is not None and constraint.concept == hidden.answer.evidence.concept:
                    request_by_criterion.setdefault(criterion.criterion_id, []).append(
                        hidden.request.fact_id
                    )

    decisions = []
    visible_ids = {item.evidence_id for item in patient_state.facts}
    for trial in case.trials:
        assessments = []
        for criterion in trial.criteria:
            result = evaluate_criterion(criterion, patient_state)
            missing_ids = []
            if result.evidence_sufficiency.value != "sufficient":
                missing_ids = request_by_criterion.get(criterion.criterion_id, [])
            assessments.append(
                CriterionAssessment(
                    criterion_id=criterion.criterion_id,
                    criterion_source_location=criterion.source_location,
                    clinical_status=result.clinical_status,
                    evidence_sufficiency=result.evidence_sufficiency,
                    evidence_ids=result.evidence_ids,
                    missing_information_ids=missing_ids,
                    rationale=(
                        "구조화된 환자 사실과 조건을 코드로 비교했다."
                        if result.configured
                        else "이 조건에는 기계 판정 규칙이 없다."
                    ),
                    review_flags=[],
                )
            )
        decisions.append(
            aggregate_trial_decision(
                trial_id=trial.trial_id,
                criteria=trial.criteria,
                assessments=assessments,
                next_action=AgentAction(
                    action=NextAction.NONE,
                    reason="후보 전체 상태 계산에서는 행동을 별도로 선택한다.",
                ),
                available_evidence_ids=visible_ids,
            )
        )
    return InteractiveSnapshot(patient_state=patient_state, decisions=decisions)


def state_with_revealed_facts(
    case: InteractiveCase,
    fact_ids: set[str] | frozenset[str],
) -> PatientState:
    """Construct a visible state from initial facts and selected answer cards."""

    selected_evidence_ids = set(case.initial_visible_evidence_ids)
    selected_evidence_ids.update(
        item.answer.evidence.evidence_id
        for item in case.hidden_facts
        if item.request.fact_id in fact_ids
    )
    return case.full_patient_state.model_copy(
        update={
            "facts": [
                item
                for item in case.full_patient_state.facts
                if item.evidence_id in selected_evidence_ids
            ]
        }
    )


def decision_signature(snapshot: InteractiveSnapshot) -> tuple[tuple[str, str, str], ...]:
    """Return the candidate and confirmation decisions that define recovery."""

    return tuple(
        (
            item.trial_id,
            item.candidate_status.value,
            item.confirmation_status.value,
        )
        for item in sorted(snapshot.decisions, key=lambda value: value.trial_id)
    )


def minimal_sufficient_fact_sets(case: InteractiveCase) -> MinimalQuestionGold:
    """Enumerate every smallest hidden-fact set matching the full oracle."""

    full = evaluate_interactive_case(case, case.full_patient_state)
    target = decision_signature(full)
    fact_ids = sorted(item.request.fact_id for item in case.hidden_facts)
    sufficient: list[frozenset[str]] = []
    for size in range(case.action_budget + 1):
        for selected in combinations(fact_ids, size):
            state = state_with_revealed_facts(case, frozenset(selected))
            snapshot = evaluate_interactive_case(case, state)
            if decision_signature(snapshot) == target:
                sufficient.append(frozenset(selected))
        if sufficient:
            break
    return MinimalQuestionGold(
        minimal_fact_sets=[sorted(item) for item in sufficient],
        recoverable_within_budget=bool(sufficient),
    )


def exact_fact_sensitivity(case: InteractiveCase) -> SensitivityProfile:
    """Measure every fact's marginal contribution over all 2^n visible states.

    The value of a state is the fraction of candidate trials whose candidate
    and confirmation statuses match the full-information oracle.  With five
    hidden facts only 32 states are needed, so no sampling approximation or
    model judgment enters this analysis.
    """

    fact_ids = sorted(item.request.fact_id for item in case.hidden_facts)
    full = evaluate_interactive_case(case, case.full_patient_state)
    target = decision_signature(full)
    trial_count = len(target)
    values: dict[frozenset[str], float] = {}
    for size in range(len(fact_ids) + 1):
        for selected in combinations(fact_ids, size):
            chosen = frozenset(selected)
            snapshot = evaluate_interactive_case(
                case, state_with_revealed_facts(case, chosen)
            )
            actual = decision_signature(snapshot)
            matches = sum(left == right for left, right in zip(actual, target, strict=True))
            values[chosen] = matches / trial_count

    public = case.public_policy_view()
    related_trial_count: dict[str, int] = {}
    criterion_to_trial = {
        criterion.criterion_id: trial.trial_id
        for trial in public.trials
        for criterion in trial.criteria
    }
    for fact in public.available_information:
        related_trial_count[fact.fact_id] = len(
            {criterion_to_trial[item] for item in fact.related_criterion_ids}
        )

    all_facts = frozenset(fact_ids)
    denominator = factorial(len(fact_ids))
    results = []
    for fact_id in fact_ids:
        marginal = 0.0
        without = [item for item in fact_ids if item != fact_id]
        for size in range(len(without) + 1):
            weight = (
                factorial(size)
                * factorial(len(fact_ids) - size - 1)
                / denominator
            )
            for selected in combinations(without, size):
                base = frozenset(selected)
                marginal += weight * (
                    values[base | {fact_id}] - values[base]
                )
        results.append(
            FactSensitivity(
                fact_id=fact_id,
                public_related_trial_count=related_trial_count[fact_id],
                standalone_recovery_gain=(
                    values[frozenset({fact_id})] - values[frozenset()]
                ),
                leave_one_out_recovery_loss=(
                    values[all_facts] - values[all_facts - {fact_id}]
                ),
                average_marginal_recovery=marginal,
            )
        )
    return SensitivityProfile(
        fact_count=len(fact_ids),
        evaluated_state_count=len(values),
        facts=results,
    )
