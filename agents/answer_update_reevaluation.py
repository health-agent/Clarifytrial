"""Answer Update & Targeted Re-evaluation Agent.

Responsibility: after the Patient Profile Understanding Agent has
normalized a free-text clarification answer into an ``AnswerUpdate``,
apply it to the session and re-evaluate ONLY the criteria affected by the
answered ``missing_variable_key`` (targeted re-evaluation across all
trials, not a full re-run).
"""

from __future__ import annotations

from models import AnswerUpdate, CriterionState, PatientSession


def apply_answer_update(
    session: PatientSession, update: AnswerUpdate
) -> PatientSession:
    """Apply a normalized answer to the shared state.

    TODO: mark the question answered, mark the pool item resolved, write
    the normalized value into the patient profile, and bump/validate the
    session-level clarification_round_count (max 3).
    """
    raise NotImplementedError("Answer application not implemented in skeleton")


def reevaluate_affected_criteria(
    session: PatientSession, update: AnswerUpdate
) -> list[CriterionState]:
    """Re-run matching only for criteria affected by the answered variable.

    TODO: look up affected criterion ids via the global missing variable
    pool, call the Criterion Matching Agent for each, then let the
    Eligibility State Tracker re-derive effects via rules.
    """
    raise NotImplementedError("Targeted re-evaluation not implemented in skeleton")
