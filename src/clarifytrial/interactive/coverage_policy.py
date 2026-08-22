"""Transparent exact planning over a small set of missing patient facts."""

from __future__ import annotations

from collections.abc import Collection
from itertools import combinations

from ..contracts import ConfirmationStatus
from .contracts import InteractivePolicyView, InteractiveSnapshot


def choose_fact_from_unresolved_sets(
    *,
    unresolved_by_trial: dict[str, set[str]],
    related_criterion_count: dict[str, int],
    public_order: list[str],
    remaining_budget: int,
) -> str | None:
    """Exact small-set planner shared by evaluation and patient workflows."""

    if remaining_budget <= 0 or not public_order or not unresolved_by_trial:
        return None
    maximum_size = min(remaining_budget, len(public_order))
    candidates = []
    for size in range(1, maximum_size + 1):
        for choice in combinations(public_order, size):
            selected = set(choice)
            closable = sum(
                unresolved <= selected for unresolved in unresolved_by_trial.values()
            )
            touched = sum(
                bool(unresolved & selected)
                for unresolved in unresolved_by_trial.values()
            )
            related_criteria = sum(
                related_criterion_count[item] for item in selected
            )
            candidates.append((closable, touched, related_criteria, choice))
    best = max(
        candidates,
        key=lambda item: (item[0], item[1], item[2], tuple(reversed(item[3]))),
    )

    def first_step_key(fact_id: str) -> tuple[int, int, int, str]:
        immediate = sum(
            unresolved == {fact_id} for unresolved in unresolved_by_trial.values()
        )
        related_trials = sum(
            fact_id in unresolved for unresolved in unresolved_by_trial.values()
        )
        return (
            -immediate,
            -related_trials,
            -related_criterion_count[fact_id],
            fact_id,
        )

    return min(best[3], key=first_step_key)


def choose_exact_coverage_fact(
    *,
    view: InteractivePolicyView,
    snapshot: InteractiveSnapshot,
    revealed_fact_ids: frozenset[str],
    remaining_budget: int,
    allowed_fact_ids: Collection[str] | None = None,
) -> str | None:
    """Choose a fact from the set that closes most trials within the budget.

    The search uses only visible pending-information IDs.  It never reads or
    predicts a hidden answer.  With five candidate facts, every subset is cheap
    to inspect and easier to audit than a learned ranking score.
    """

    if remaining_budget <= 0:
        return None
    public_order = [
        item.fact_id
        for item in view.available_information
        if item.fact_id not in revealed_fact_ids
        and (allowed_fact_ids is None or item.fact_id in allowed_fact_ids)
    ]
    if not public_order:
        return None
    available = set(public_order)
    unresolved_by_trial = {}
    for decision in snapshot.decisions:
        if decision.confirmation_status in {
            ConfirmationStatus.CONFIRMED,
            ConfirmationStatus.INELIGIBLE,
        }:
            continue
        unresolved = {
            item.fact_id
            for item in decision.pending_information
            if item.fact_id in available
        }
        unresolved.update(
            fact_id
            for assessment in decision.criterion_assessments
            for fact_id in assessment.missing_information_ids
            if fact_id in available
        )
        if unresolved:
            unresolved_by_trial[decision.trial_id] = unresolved
    if not unresolved_by_trial:
        return None

    criterion_count = {
        item.fact_id: len(item.related_criterion_ids)
        for item in view.available_information
    }
    return choose_fact_from_unresolved_sets(
        unresolved_by_trial=unresolved_by_trial,
        related_criterion_count=criterion_count,
        public_order=public_order,
        remaining_budget=remaining_budget,
    )


__all__ = ["choose_exact_coverage_fact", "choose_fact_from_unresolved_sets"]
