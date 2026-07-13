"""Tests for missing-information detection and the clarification queue."""

import pytest

from agents.clarification_question import build_global_clarification_queue
from agents.missing_information_detection import build_global_missing_variable_pool
from models import (
    CriterionMatchStatus,
    CriterionState,
    CriterionType,
    EligibilityEffect,
    QuestionStatus,
)

API_KEY_ENV_VARS = [
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "GOOGLE_API_KEY",
]


@pytest.fixture(autouse=True)
def no_api_keys(monkeypatch):
    """The layer must work with no API keys in the environment."""
    for var in API_KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def unknown_state(cid: str, trial_id: str, keys: list[str]) -> CriterionState:
    return CriterionState(
        criterion_id=cid,
        trial_id=trial_id,
        criterion_type=CriterionType.inclusion,
        criterion_match_status=CriterionMatchStatus.unknown,
        eligibility_effect=EligibilityEffect.uncertain,
        missing_variable_keys=keys,
    )


def met_state(cid: str, trial_id: str) -> CriterionState:
    return CriterionState(
        criterion_id=cid,
        trial_id=trial_id,
        criterion_type=CriterionType.inclusion,
        criterion_match_status=CriterionMatchStatus.met,
        eligibility_effect=EligibilityEffect.supports_eligibility,
    )


SHARED_KEY_STATES = [
    unknown_state("T1-INC-02", "T1", ["ecog_performance_status"]),
    unknown_state("T2-INC-03", "T2", ["ecog_performance_status"]),
    unknown_state("T2-INC-04", "T2", ["renal_function"]),
    unknown_state("T1-EXC-01", "T1", ["quality_of_life_score"]),
]


def test_pool_dedups_repeated_keys_with_traceability():
    pool = build_global_missing_variable_pool(SHARED_KEY_STATES)
    assert len(pool) == 3
    item = pool["ecog_performance_status"]
    assert item.affected_criterion_ids == ["T1-INC-02", "T2-INC-03"]
    assert item.affected_trial_ids == ["T1", "T2"]


def test_unknown_without_missing_key_is_skipped_gracefully():
    bare_unknown = unknown_state("T1-INC-09", "T1", [])
    pool = build_global_missing_variable_pool([bare_unknown])
    assert pool == {}


def test_non_unknown_states_excluded_from_pool():
    # A met state carrying stale keys must not contribute.
    stale = met_state("T1-INC-01", "T1")
    stale.missing_variable_keys = ["age"]
    pool = build_global_missing_variable_pool([stale])
    assert pool == {}


def test_deterministic_priority_assignment():
    pool = build_global_missing_variable_pool(SHARED_KEY_STATES)
    # multiple criteria/trials -> high
    assert pool["ecog_performance_status"].priority == "high"
    # single criterion, common screening variable -> medium
    assert pool["renal_function"].priority == "medium"
    # single criterion, uncommon variable -> low
    assert pool["quality_of_life_score"].priority == "low"


def test_queue_one_question_per_variable_no_repeats():
    pool = build_global_missing_variable_pool(SHARED_KEY_STATES)
    questions = build_global_clarification_queue(pool)
    assert len(questions) == 3
    keys = [q.missing_variable_key for q in questions]
    assert len(keys) == len(set(keys))
    # The shared key yields ONE question covering criteria of both trials.
    ecog_q = next(q for q in questions if q.missing_variable_key == "ecog_performance_status")
    assert ecog_q.affected_criterion_ids == ["T1-INC-02", "T2-INC-03"]
    assert all(q.status == QuestionStatus.pending for q in questions)


def test_stable_question_ids_and_deterministic_ordering():
    pool = build_global_missing_variable_pool(SHARED_KEY_STATES)
    q1 = build_global_clarification_queue(pool)
    q2 = build_global_clarification_queue(pool)
    assert [q.question_id for q in q1] == ["Q-001", "Q-002", "Q-003"]
    assert [q.question_id for q in q1] == [q.question_id for q in q2]
    assert [q.missing_variable_key for q in q1] == [q.missing_variable_key for q in q2]
    # Priority ordering: high first, low last.
    assert q1[0].missing_variable_key == "ecog_performance_status"
    assert q1[-1].missing_variable_key == "quality_of_life_score"


def test_generic_fallback_for_unknown_variable_names():
    pool = build_global_missing_variable_pool(
        [unknown_state("T1-INC-05", "T1", ["quality_of_life_score"])]
    )
    (question,) = build_global_clarification_queue(pool)
    assert "quality of life score" in question.question_text
    assert question.target_profile_field == "variables.quality_of_life_score"


def test_optional_max_rounds_respected():
    pool = build_global_missing_variable_pool(SHARED_KEY_STATES)
    assert build_global_clarification_queue(pool, round_number=3, max_rounds=3) != []
    assert build_global_clarification_queue(pool, round_number=4, max_rounds=3) == []


def test_default_question_rounds_have_no_fixed_cap():
    pool = build_global_missing_variable_pool(SHARED_KEY_STATES)
    assert build_global_clarification_queue(pool, round_number=4) != []


def test_demo_script_runs_and_writes_report():
    from scripts.run_missing_info_clarification_demo import REPORT_PATH, main

    questions = main()
    assert len(questions) >= 1
    keys = [q.missing_variable_key for q in questions]
    assert len(keys) == len(set(keys))
    assert REPORT_PATH.is_file()
    assert "SYNTHETIC" in REPORT_PATH.read_text(encoding="utf-8")
