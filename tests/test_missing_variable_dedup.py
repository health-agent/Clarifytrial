"""Tests for global missing-variable deduplication across trials."""

from models import (
    CriterionMatchStatus,
    CriterionState,
    CriterionType,
    EligibilityEffect,
)
from rules import deduplicate_missing_variables


def unknown_state(criterion_id: str, trial_id: str, keys: list[str]) -> CriterionState:
    return CriterionState(
        criterion_id=criterion_id,
        trial_id=trial_id,
        criterion_type=CriterionType.inclusion,
        criterion_match_status=CriterionMatchStatus.unknown,
        eligibility_effect=EligibilityEffect.uncertain,
        missing_variable_keys=keys,
    )


def test_missing_variable_key_deduplicated_globally_across_trials():
    states = [
        unknown_state("T1-INC-01", "T1", ["ecog_performance_status"]),
        unknown_state("T2-INC-03", "T2", ["ecog_performance_status"]),
        unknown_state("T2-INC-04", "T2", ["creatinine_clearance"]),
    ]

    pool = deduplicate_missing_variables(states)

    # Shared key appears exactly once, merging criteria from both trials.
    assert set(pool.keys()) == {"ecog_performance_status", "creatinine_clearance"}
    item = pool["ecog_performance_status"]
    assert item.affected_criterion_ids == ["T1-INC-01", "T2-INC-03"]
    assert item.affected_trial_ids == ["T1", "T2"]


def test_duplicate_keys_within_one_trial_are_merged_without_duplication():
    states = [
        unknown_state("T1-INC-01", "T1", ["hba1c"]),
        unknown_state("T1-INC-02", "T1", ["hba1c"]),
    ]

    pool = deduplicate_missing_variables(states)

    assert list(pool.keys()) == ["hba1c"]
    assert pool["hba1c"].affected_criterion_ids == ["T1-INC-01", "T1-INC-02"]
    assert pool["hba1c"].affected_trial_ids == ["T1"]
