"""Clarification Question Agent.

Responsibility: turn pending items of the global missing variable pool into
``FollowUpQuestion`` objects and manage them in the single GLOBAL
clarification queue (locked invariant: questions are global, never
per-trial). Question rounds are unbounded by default; experiments may pass
an optional limit when comparing stopping policies.

Current implementation: deterministic question templates for common
variables with a generic fallback. LLM question phrasing, driven by
``prompts/clarification_question.md``, will replace the templates behind
the same typed contract. Answer handling and re-evaluation are NOT done
here (that is the Answer Update & Targeted Re-evaluation Agent).
"""

from __future__ import annotations

from typing import Any, Optional

from models import (
    FollowUpQuestion,
    GlobalMissingVariablePoolItem,
    PatientSession,
    QuestionStatus,
)

# Deterministic patient-facing templates for common variables:
# (question_text, expected_answer_type, allowed_values_or_schema)
_QUESTION_TEMPLATES: dict[str, tuple[str, str, Optional[Any]]] = {
    "ecog_performance_status": (
        "How would you rate your current level of daily activity, from 0 "
        "(fully active) to 4 (fully bedridden)?",
        "integer",
        [0, 1, 2, 3, 4],
    ),
    "renal_function": (
        "Do you have a recent kidney function test result (such as "
        "creatinine clearance or eGFR)? If so, what was the value?",
        "number",
        None,
    ),
    "biomarker_status": (
        "Have you had biomarker or genetic testing for your condition, "
        "and if so, what were the results?",
        "string",
        None,
    ),
    "prior_treatment": (
        "Have you received any previous treatment for your condition "
        "(for example chemotherapy, surgery, or radiation)? Please list them.",
        "string",
        None,
    ),
    "current_treatment": (
        "What treatments or medications are you currently taking?",
        "string",
        None,
    ),
    "disease_stage": (
        "Has your care team told you the stage of your condition? If so, "
        "what stage?",
        "string",
        None,
    ),
    "age": ("What is your age in years?", "integer", None),
    "sex": ("What is your sex?", "string", ["male", "female"]),
    "hba1c": (
        "What was your most recent HbA1c value, if you know it?",
        "number",
        None,
    ),
    "comorbidities": (
        "Do you have any other medical conditions? Please list them.",
        "string",
        None,
    ),
    "pregnancy_status": (
        "Are you currently pregnant or breastfeeding?",
        "string",
        ["yes", "no", "not applicable"],
    ),
}

_PRIORITY_ORDER = {"high": 0, "medium": 1, "low": 2, None: 3}


def build_global_clarification_queue(
    missing_variable_pool: dict[str, GlobalMissingVariablePoolItem],
    round_number: int = 1,
    max_rounds: Optional[int] = None,
) -> list[FollowUpQuestion]:
    """Build the global clarification queue from the missing-variable pool.

    Guarantees:
    - exactly ONE question per missing_variable_key (the pool is already
      deduplicated at session level, so a variable needed by many
      criteria/trials is asked once);
    - traceability preserved via ``affected_criterion_ids``;
    - deterministic ordering (priority high->medium->low, then key) and
      stable ids Q-001, Q-002, ...;
    - no fixed round limit by default;
    - if an experiment supplies ``max_rounds`` and the current round exceeds
      it, no new questions are created;
    - no answer handling, no re-evaluation, no eligibility decisions.

    TODO: LLM question phrasing via prompts/clarification_question.md
    (plain-language wording tuned to the source criteria), keeping ids,
    ordering, and dedup semantics exactly as here.
    """
    if max_rounds is not None and round_number > max_rounds:
        return []

    ordered = sorted(
        missing_variable_pool.values(),
        key=lambda item: (
            _PRIORITY_ORDER.get(item.priority, 3),
            item.missing_variable_key,
        ),
    )

    questions: list[FollowUpQuestion] = []
    for i, item in enumerate(ordered, start=1):
        template = _QUESTION_TEMPLATES.get(item.missing_variable_key)
        if template:
            text, answer_type, allowed = template
        else:
            display = item.missing_variable_key.replace("_", " ")
            text = f"Could you provide the following information: {display}?"
            answer_type, allowed = "string", None
        questions.append(
            FollowUpQuestion(
                question_id=f"Q-{i:03d}",
                missing_variable_key=item.missing_variable_key,
                target_profile_field=f"variables.{item.missing_variable_key}",
                expected_answer_type=answer_type,
                allowed_values_or_schema=allowed,
                affected_criterion_ids=list(item.affected_criterion_ids),
                question_text=text,
                priority_rank=i,
                status=QuestionStatus.pending,
            )
        )
    return questions


def generate_questions(
    pool: dict[str, GlobalMissingVariablePoolItem],
) -> list[FollowUpQuestion]:
    """Generate one question per pending missing variable.

    Thin wrapper over :func:`build_global_clarification_queue` (round 1).
    """
    return build_global_clarification_queue(pool)


def enqueue_questions(
    session: PatientSession, questions: list[FollowUpQuestion]
) -> PatientSession:
    """Append new questions to the global clarification queue.

    TODO: dedupe by missing_variable_key against already-queued questions
    and re-sort the queue by priority_rank (Eligibility State Tracker
    integration; not needed for the current offline demo).
    """
    raise NotImplementedError("Queue management not implemented in skeleton")
