"""Matcher and criterion-judgment agent."""

from __future__ import annotations

from .base import CriterionAssessmentBatch, StructuredAgent


class MatcherJudgeAgent(StructuredAgent[CriterionAssessmentBatch]):
    """Judge the related criterion bundle for one supplied candidate trial."""

    agent_name = "matcher_judge"
    prompt_id = "prompts/matcher_judge.md"
    response_model = CriterionAssessmentBatch
