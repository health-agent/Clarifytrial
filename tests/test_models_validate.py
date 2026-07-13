"""Validate the shipped demo session against the Pydantic models."""

import json
from pathlib import Path

from models import PatientSession, QuestionStatus

EXAMPLE_PATH = (
    Path(__file__).resolve().parent.parent / "examples" / "demo_patient_session.json"
)


def load_session() -> PatientSession:
    data = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    return PatientSession.model_validate(data)


def test_demo_session_validates_against_patient_session_model():
    session = load_session()
    assert session.patient_id == "synthetic-patient-001"
    assert session.clarification_round_count >= 0


def test_session_round_count_has_no_fixed_upper_bound():
    session = load_session().model_copy(update={"clarification_round_count": 4})
    assert PatientSession.model_validate(session.model_dump()).clarification_round_count == 4


def test_demo_session_respects_locked_invariants():
    session = load_session()

    # Two mock trials under trial_states_by_trial_id.
    assert len(session.trial_states_by_trial_id) == 2

    # At least one unknown criterion.
    all_states = [
        s
        for trial in session.trial_states_by_trial_id.values()
        for s in trial.criterion_states
    ]
    assert any(s.criterion_match_status == "unknown" for s in all_states)

    # One global missing variable deduplicated across both trials.
    item = session.global_missing_variable_pool["ecog_performance_status"]
    assert set(item.affected_trial_ids) == set(session.trial_states_by_trial_id)

    # One pending follow-up question in the GLOBAL clarification queue.
    assert len(session.global_clarification_queue) == 1
    assert session.global_clarification_queue[0].status == QuestionStatus.pending

    # Two trial recommendations.
    assert len(session.trial_recommendations) == 2
