"""Pure rule functions for ClarifyTrial Agent v1.2-final.

All functions here are deterministic and side-effect free. They implement
the locked rule mappings and recommendation precedence of v1.2-final.
"""

from __future__ import annotations

from typing import Iterable, Optional

from models import (
    CriterionMatchStatus,
    CriterionState,
    CriterionType,
    EligibilityEffect,
    GlobalMissingVariablePoolItem,
    Recommendation,
    ReviewReason,
    TrialRecommendation,
)


def derive_eligibility_effect(
    criterion_type: CriterionType,
    criterion_match_status: CriterionMatchStatus,
    max_rounds_exceeded: bool = False,
) -> tuple[EligibilityEffect, bool, Optional[ReviewReason]]:
    """Map (criterion_type, match_status) to an eligibility effect.

    Returns ``(eligibility_effect, review_required, review_reason)``.

    Locked rule mappings:
    - inclusion + met      -> supports_eligibility
    - inclusion + unmet    -> blocks_eligibility
    - inclusion + unknown  -> uncertain
    - exclusion + met      -> blocks_eligibility
    - exclusion + unmet    -> supports_eligibility
    - exclusion + unknown  -> uncertain
    - conflict             -> uncertain, review_required, conflicting_evidence
    - unknown after max rounds -> uncertain, review_required, max_rounds_exceeded
    - not_applicable       -> neutral
    """
    if criterion_match_status == CriterionMatchStatus.conflict:
        return (
            EligibilityEffect.uncertain,
            True,
            ReviewReason.conflicting_evidence,
        )

    if criterion_match_status == CriterionMatchStatus.not_applicable:
        return (EligibilityEffect.neutral, False, None)

    if criterion_match_status == CriterionMatchStatus.unknown:
        if max_rounds_exceeded:
            return (
                EligibilityEffect.uncertain,
                True,
                ReviewReason.max_rounds_exceeded,
            )
        return (EligibilityEffect.uncertain, False, None)

    met = criterion_match_status == CriterionMatchStatus.met
    if criterion_type == CriterionType.inclusion:
        effect = (
            EligibilityEffect.supports_eligibility
            if met
            else EligibilityEffect.blocks_eligibility
        )
    else:  # exclusion
        effect = (
            EligibilityEffect.blocks_eligibility
            if met
            else EligibilityEffect.supports_eligibility
        )
    return (effect, False, None)


def deduplicate_missing_variables(
    criterion_states: Iterable[CriterionState],
) -> dict[str, GlobalMissingVariablePoolItem]:
    """Build the global missing variable pool keyed by ``missing_variable_key``.

    The same key appearing in criteria across multiple trials is merged into
    a single pool item, accumulating the affected criterion and trial ids.
    """
    pool: dict[str, GlobalMissingVariablePoolItem] = {}
    for state in criterion_states:
        for key in state.missing_variable_keys:
            item = pool.get(key)
            if item is None:
                item = GlobalMissingVariablePoolItem(missing_variable_key=key)
                pool[key] = item
            if state.criterion_id not in item.affected_criterion_ids:
                item.affected_criterion_ids.append(state.criterion_id)
            if state.trial_id not in item.affected_trial_ids:
                item.affected_trial_ids.append(state.trial_id)
    return pool


def compute_trial_recommendation(
    trial_id: str,
    criterion_states: list[CriterionState],
    trial_relevance_score: float,
    uncertainty_threshold: float = 0.4,
) -> TrialRecommendation:
    """Apply the v1.2-final recommendation precedence, exactly in order:

    1. any eligibility_effect == blocks_eligibility -> likely_ineligible
    2. else any review_required == true             -> needs_human_review
    3. else uncertainty ratio > threshold           -> uncertain
    4. else                                         -> likely_eligible

    ``trial_relevance_score`` contributes to ``ranking_score`` only; it never
    changes the recommendation (hard eligibility).
    """
    blocking = [
        s.criterion_id
        for s in criterion_states
        if s.eligibility_effect == EligibilityEffect.blocks_eligibility
    ]
    supporting = [
        s.criterion_id
        for s in criterion_states
        if s.eligibility_effect == EligibilityEffect.supports_eligibility
    ]
    uncertain = [
        s.criterion_id
        for s in criterion_states
        if s.eligibility_effect == EligibilityEffect.uncertain
    ]
    review_required = any(s.review_required for s in criterion_states)

    non_neutral = [
        s
        for s in criterion_states
        if s.eligibility_effect != EligibilityEffect.neutral
    ]
    uncertainty_ratio = (
        len(uncertain) / len(non_neutral) if non_neutral else 0.0
    )

    if blocking:
        recommendation = Recommendation.likely_ineligible
    elif review_required:
        recommendation = Recommendation.needs_human_review
    elif uncertainty_ratio > uncertainty_threshold:
        recommendation = Recommendation.uncertain
    else:
        recommendation = Recommendation.likely_eligible

    pending_questions = sorted(
        {key for s in criterion_states for key in s.missing_variable_keys}
    )

    return TrialRecommendation(
        trial_id=trial_id,
        recommendation=recommendation,
        trial_relevance_score=trial_relevance_score,
        ranking_score=trial_relevance_score * (1.0 - uncertainty_ratio / 2.0),
        hard_filter_triggered=bool(blocking),
        blocking_criteria=blocking,
        supporting_criteria=supporting,
        uncertain_criteria=uncertain,
        pending_questions=pending_questions,
    )


def rank_trials(
    trial_recommendations: list[TrialRecommendation],
) -> list[TrialRecommendation]:
    """Rank trials: blocked (likely_ineligible) trials always sort below
    non-blocked trials; within each group, higher ``ranking_score`` first.

    Returns a new sorted list with 1-based ``rank`` assigned.
    """
    ranked = sorted(
        trial_recommendations,
        key=lambda r: (
            r.recommendation == Recommendation.likely_ineligible,
            -r.ranking_score,
        ),
    )
    return [
        rec.model_copy(update={"rank": i + 1}) for i, rec in enumerate(ranked)
    ]
