"""Tests for rules.derive_eligibility_effect (locked v1.2-final mappings)."""

import pytest

from models import CriterionMatchStatus, CriterionType, EligibilityEffect, ReviewReason
from rules import derive_eligibility_effect


def test_inclusion_met_supports_eligibility():
    effect, review_required, reason = derive_eligibility_effect(
        CriterionType.inclusion, CriterionMatchStatus.met
    )
    assert effect == EligibilityEffect.supports_eligibility
    assert review_required is False
    assert reason is None


def test_inclusion_unmet_blocks_eligibility():
    effect, review_required, reason = derive_eligibility_effect(
        CriterionType.inclusion, CriterionMatchStatus.unmet
    )
    assert effect == EligibilityEffect.blocks_eligibility
    assert review_required is False
    assert reason is None


def test_exclusion_met_blocks_eligibility():
    effect, review_required, reason = derive_eligibility_effect(
        CriterionType.exclusion, CriterionMatchStatus.met
    )
    assert effect == EligibilityEffect.blocks_eligibility
    assert review_required is False
    assert reason is None


def test_exclusion_unmet_supports_eligibility():
    effect, review_required, reason = derive_eligibility_effect(
        CriterionType.exclusion, CriterionMatchStatus.unmet
    )
    assert effect == EligibilityEffect.supports_eligibility
    assert review_required is False
    assert reason is None


@pytest.mark.parametrize(
    "criterion_type", [CriterionType.inclusion, CriterionType.exclusion]
)
def test_unknown_is_uncertain_without_a_configured_stop(criterion_type):
    effect, review_required, reason = derive_eligibility_effect(
        criterion_type, CriterionMatchStatus.unknown
    )
    assert effect == EligibilityEffect.uncertain
    assert review_required is False
    assert reason is None


@pytest.mark.parametrize(
    "criterion_type", [CriterionType.inclusion, CriterionType.exclusion]
)
def test_conflict_uncertain_with_review(criterion_type):
    effect, review_required, reason = derive_eligibility_effect(
        criterion_type, CriterionMatchStatus.conflict
    )
    assert effect == EligibilityEffect.uncertain
    assert review_required is True
    assert reason == ReviewReason.conflicting_evidence


def test_unknown_after_an_optional_round_limit_requires_review():
    effect, review_required, reason = derive_eligibility_effect(
        CriterionType.inclusion,
        CriterionMatchStatus.unknown,
        max_rounds_exceeded=True,
    )
    assert effect == EligibilityEffect.uncertain
    assert review_required is True
    assert reason == ReviewReason.max_rounds_exceeded


def test_not_applicable_is_neutral():
    effect, review_required, reason = derive_eligibility_effect(
        CriterionType.exclusion, CriterionMatchStatus.not_applicable
    )
    assert effect == EligibilityEffect.neutral
    assert review_required is False
    assert reason is None
