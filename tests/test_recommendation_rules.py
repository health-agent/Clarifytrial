"""Tests for rules.compute_trial_recommendation and rules.rank_trials."""

from models import (
    CriterionMatchStatus,
    CriterionState,
    CriterionType,
    EligibilityEffect,
    Recommendation,
    ReviewReason,
)
from rules import compute_trial_recommendation, rank_trials


def make_state(
    criterion_id: str,
    effect: EligibilityEffect,
    review_required: bool = False,
    review_reason: ReviewReason | None = None,
) -> CriterionState:
    return CriterionState(
        criterion_id=criterion_id,
        trial_id="T1",
        criterion_type=CriterionType.inclusion,
        criterion_match_status=CriterionMatchStatus.unknown,
        eligibility_effect=effect,
        review_required=review_required,
        review_reason=review_reason,
    )


def test_any_block_yields_likely_ineligible():
    states = [
        make_state("c1", EligibilityEffect.supports_eligibility),
        make_state("c2", EligibilityEffect.blocks_eligibility),
        # Even with review_required, a block takes precedence.
        make_state(
            "c3",
            EligibilityEffect.uncertain,
            review_required=True,
            review_reason=ReviewReason.conflicting_evidence,
        ),
    ]
    rec = compute_trial_recommendation("T1", states, trial_relevance_score=0.9)
    assert rec.recommendation == Recommendation.likely_ineligible
    assert rec.hard_filter_triggered is True
    assert rec.blocking_criteria == ["c2"]


def test_review_required_without_block_yields_needs_human_review():
    states = [
        make_state("c1", EligibilityEffect.supports_eligibility),
        make_state(
            "c2",
            EligibilityEffect.uncertain,
            review_required=True,
            review_reason=ReviewReason.conflicting_evidence,
        ),
    ]
    rec = compute_trial_recommendation("T1", states, trial_relevance_score=0.5)
    assert rec.recommendation == Recommendation.needs_human_review
    assert rec.hard_filter_triggered is False


def test_high_uncertainty_without_block_or_review_yields_uncertain():
    states = [
        make_state("c1", EligibilityEffect.supports_eligibility),
        make_state("c2", EligibilityEffect.uncertain),
        make_state("c3", EligibilityEffect.uncertain),
    ]
    # uncertainty ratio 2/3 > 0.4
    rec = compute_trial_recommendation("T1", states, trial_relevance_score=0.5)
    assert rec.recommendation == Recommendation.uncertain


def test_low_uncertainty_yields_likely_eligible():
    states = [
        make_state("c1", EligibilityEffect.supports_eligibility),
        make_state("c2", EligibilityEffect.supports_eligibility),
        make_state("c3", EligibilityEffect.supports_eligibility),
        make_state("c4", EligibilityEffect.uncertain),
    ]
    # uncertainty ratio 1/4 <= 0.4
    rec = compute_trial_recommendation("T1", states, trial_relevance_score=0.5)
    assert rec.recommendation == Recommendation.likely_eligible


def test_relevance_score_never_overrides_hard_block():
    blocked = compute_trial_recommendation(
        "T-blocked",
        [make_state("c1", EligibilityEffect.blocks_eligibility)],
        trial_relevance_score=1.0,
    )
    assert blocked.recommendation == Recommendation.likely_ineligible


def test_rank_trials_places_blocked_below_and_sorts_by_ranking_score():
    blocked_high_relevance = compute_trial_recommendation(
        "T-blocked",
        [make_state("c1", EligibilityEffect.blocks_eligibility)],
        trial_relevance_score=1.0,
    )
    eligible_low = compute_trial_recommendation(
        "T-low",
        [make_state("c1", EligibilityEffect.supports_eligibility)],
        trial_relevance_score=0.3,
    )
    eligible_high = compute_trial_recommendation(
        "T-high",
        [make_state("c1", EligibilityEffect.supports_eligibility)],
        trial_relevance_score=0.7,
    )

    ranked = rank_trials([blocked_high_relevance, eligible_low, eligible_high])

    assert [r.trial_id for r in ranked] == ["T-high", "T-low", "T-blocked"]
    assert [r.rank for r in ranked] == [1, 2, 3]
