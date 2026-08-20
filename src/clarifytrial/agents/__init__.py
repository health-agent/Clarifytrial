"""Role-specific structured agents for ClarifyTrial v5."""

from .base import (
    AgentResult,
    CriterionAssessmentBatch,
    CoordinatorDecision,
    CoordinatorRoute,
    ReviewDecision,
    ReviewOutcome,
    StructuredAgent,
)
from .coordinator import CoordinatorAgent
from .matcher_judge import MatcherJudgeAgent
from .next_evidence import NextEvidenceAgent
from .selective_reviewer import SelectiveReviewerAgent

__all__ = [
    "AgentResult",
    "CoordinatorAgent",
    "CoordinatorDecision",
    "CoordinatorRoute",
    "CriterionAssessmentBatch",
    "MatcherJudgeAgent",
    "NextEvidenceAgent",
    "ReviewDecision",
    "ReviewOutcome",
    "SelectiveReviewerAgent",
    "StructuredAgent",
]
