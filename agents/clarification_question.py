"""Clarification Question Agent.

Responsibility: turn pending items of the global missing variable pool into
``FollowUpQuestion`` objects and manage them in the single GLOBAL
clarification queue (locked invariant: questions are global, never
per-trial). Question rounds are bounded by the session-level
``clarification_round_count`` (max 3).
"""

from __future__ import annotations

from models import FollowUpQuestion, GlobalMissingVariablePoolItem, PatientSession


def generate_questions(
    pool: dict[str, GlobalMissingVariablePoolItem],
) -> list[FollowUpQuestion]:
    """Generate one question per pending missing variable.

    TODO: LLM call to phrase patient-friendly question_text, choose
    expected_answer_type / allowed_values_or_schema, and assign
    priority_rank (e.g. by number of affected criteria/trials).
    """
    raise NotImplementedError("Question generation not implemented in skeleton")


def enqueue_questions(
    session: PatientSession, questions: list[FollowUpQuestion]
) -> PatientSession:
    """Append new questions to the global clarification queue.

    TODO: dedupe by missing_variable_key against already-queued questions
    and re-sort the queue by priority_rank.
    """
    raise NotImplementedError("Queue management not implemented in skeleton")
