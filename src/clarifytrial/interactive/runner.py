"""Execute and score clarification policies on fixed synthetic cases."""

from __future__ import annotations

from collections.abc import Iterable

from ..contracts import CandidateStatus, ConfirmationStatus, NextAction
from ..environment import (
    HiddenPatientEnvironment,
    PublicQuestionCatalog,
    SyntheticInformationTools,
)
from .contracts import (
    InteractiveActionRecord,
    InteractiveBenchmarkSummary,
    InteractiveCase,
    InteractivePolicyRun,
    InteractiveRunMetrics,
    InteractiveSnapshot,
)
from .oracle import (
    decision_signature,
    evaluate_interactive_case,
    exact_fact_sensitivity,
    minimal_sufficient_fact_sets,
)
from .policies import QuestionPolicy


def _score(
    initial: InteractiveSnapshot,
    final: InteractiveSnapshot,
    full: InteractiveSnapshot,
    selected_fact_ids: list[str],
    minimal_sets: list[list[str]],
    sensitivity: dict[str, float],
    action_budget: int,
) -> InteractiveRunMetrics:
    initial_by_id = {item.trial_id: item for item in initial.decisions}
    final_by_id = {item.trial_id: item for item in final.decisions}
    full_by_id = {item.trial_id: item for item in full.decisions}
    trial_ids = sorted(full_by_id)
    trial_status_matches = sum(
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
    candidate_matches = sum(
        final_by_id[item].candidate_status is full_by_id[item].candidate_status
        for item in trial_ids
    )
    confirmation_matches = sum(
        final_by_id[item].confirmation_status
        is full_by_id[item].confirmation_status
        for item in trial_ids
    )
    selected = set(selected_fact_ids)
    if minimal_sets:
        minimal = [set(item) for item in minimal_sets]
        necessary_recall = max(
            len(selected & item) / len(item) if item else 1.0 for item in minimal
        )
        unnecessary = min(len(selected - item) for item in minimal)
    else:
        necessary_recall = 0.0
        unnecessary = len(selected)
    optimal_impact = sum(sorted(sensitivity.values(), reverse=True)[:action_budget])
    selected_impact = sum(sensitivity.get(item, 0.0) for item in selected)
    impact_capture = (
        min(1.0, max(0.0, selected_impact / optimal_impact))
        if optimal_impact > 0
        else 1.0
    )
    return InteractiveRunMetrics(
        trial_status_recovery=trial_status_matches / len(trial_ids),
        candidate_status_recovery=candidate_matches / len(trial_ids),
        confirmation_status_recovery=confirmation_matches / len(trial_ids),
        necessary_fact_recall=necessary_recall,
        unnecessary_action_count=unnecessary,
        false_candidate_removals=sum(
            final_by_id[item].candidate_status is CandidateStatus.REMOVE
            and full_by_id[item].candidate_status is not CandidateStatus.REMOVE
            for item in trial_ids
        ),
        missed_exclusions=sum(
            final_by_id[item].candidate_status is not CandidateStatus.REMOVE
            and full_by_id[item].candidate_status is CandidateStatus.REMOVE
            for item in trial_ids
        ),
        premature_confirmations=sum(
            final_by_id[item].confirmation_status is ConfirmationStatus.CONFIRMED
            and full_by_id[item].confirmation_status
            is not ConfirmationStatus.CONFIRMED
            for item in trial_ids
        ),
        unresolved_to_resolved=sum(
            initial_by_id[item].confirmation_status
            is ConfirmationStatus.NOT_CONFIRMED
            and final_by_id[item].confirmation_status
            in {ConfirmationStatus.CONFIRMED, ConfirmationStatus.INELIGIBLE}
            for item in trial_ids
        ),
        action_count=len(selected_fact_ids),
        realized_impact_capture=impact_capture,
    )


def run_interactive_policy(
    case: InteractiveCase,
    policy: QuestionPolicy,
) -> InteractivePolicyRun:
    """Run one bounded policy through the real hidden-answer environment."""

    gold = minimal_sufficient_fact_sets(case)
    if not gold.recoverable_within_budget:
        raise ValueError(f"{case.case_id} cannot reach its oracle within the budget")
    initial = evaluate_interactive_case(case, case.initial_patient_state())
    full = evaluate_interactive_case(case, case.full_patient_state)
    sensitivity = exact_fact_sensitivity(case)
    state = initial.patient_state
    snapshot = initial
    public_view = case.public_policy_view()
    tools = SyntheticInformationTools(
        PublicQuestionCatalog(item.request for item in case.hidden_facts),
        HiddenPatientEnvironment(item.answer for item in case.hidden_facts),
    )
    revealed: set[str] = set()
    history: list[InteractiveActionRecord] = []
    for step in range(1, case.action_budget + 1):
        action = policy.select(public_view, snapshot, frozenset(revealed))
        if action.action in {NextAction.NONE, NextAction.DEFER}:
            break
        result = tools.execute(action, state)
        history.append(
            InteractiveActionRecord(
                step=step,
                action=action.action,
                target_fact_id=action.target_fact_id,
                result=result,
            )
        )
        if not result.new_facts:
            break
        assert action.target_fact_id is not None
        revealed.add(action.target_fact_id)
        state = result.patient_state
        snapshot = evaluate_interactive_case(case, state)

    selected = [item.target_fact_id for item in history if item.target_fact_id]
    metrics = _score(
        initial,
        snapshot,
        full,
        selected,
        gold.minimal_fact_sets,
        {
            item.fact_id: item.average_marginal_recovery
            for item in sensitivity.facts
        },
        case.action_budget,
    )
    return InteractivePolicyRun(
        case_id=case.case_id,
        disease_group=case.disease_group,
        policy_id=policy.policy_id,
        initial_snapshot=initial,
        final_snapshot=snapshot,
        full_information_snapshot=full,
        question_gold=gold,
        sensitivity=sensitivity,
        action_history=history,
        usage=list(policy.usage),
        metrics=metrics,
    )


def summarize_interactive_runs(
    runs: Iterable[InteractivePolicyRun],
) -> InteractiveBenchmarkSummary:
    """Aggregate runs from exactly one declared policy."""

    rows = list(runs)
    if not rows:
        raise ValueError("at least one run is required")
    policy_ids = {item.policy_id for item in rows}
    if len(policy_ids) != 1:
        raise ValueError("all runs must use the same policy")
    usages = [usage for item in rows for usage in item.usage]

    def token_sum(attribute: str) -> int | None:
        values = [getattr(item, attribute) for item in usages]
        known = [item for item in values if item is not None]
        return sum(known) if known else None

    total_tokens = None
    explicit_totals = [getattr(item, "total_tokens", None) for item in usages]
    if any(item is not None for item in explicit_totals):
        total_tokens = sum(item for item in explicit_totals if item is not None)
    return InteractiveBenchmarkSummary(
        policy_id=next(iter(policy_ids)),
        case_count=len(rows),
        mean_trial_status_recovery=sum(
            item.metrics.trial_status_recovery for item in rows
        )
        / len(rows),
        mean_candidate_status_recovery=sum(
            item.metrics.candidate_status_recovery for item in rows
        )
        / len(rows),
        mean_confirmation_status_recovery=sum(
            item.metrics.confirmation_status_recovery for item in rows
        )
        / len(rows),
        mean_necessary_fact_recall=sum(
            item.metrics.necessary_fact_recall for item in rows
        )
        / len(rows),
        mean_realized_impact_capture=sum(
            item.metrics.realized_impact_capture for item in rows
        )
        / len(rows),
        total_unnecessary_actions=sum(
            item.metrics.unnecessary_action_count for item in rows
        ),
        total_false_candidate_removals=sum(
            item.metrics.false_candidate_removals for item in rows
        ),
        total_missed_exclusions=sum(
            item.metrics.missed_exclusions for item in rows
        ),
        total_premature_confirmations=sum(
            item.metrics.premature_confirmations for item in rows
        ),
        total_actions=sum(item.metrics.action_count for item in rows),
        total_input_tokens=token_sum("input_tokens"),
        total_output_tokens=token_sum("output_tokens"),
        total_reasoning_tokens=token_sum("thinking_tokens"),
        total_tokens=total_tokens,
    )
