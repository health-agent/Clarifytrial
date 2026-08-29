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
from .candidate_relevance import (
    CandidateRelevanceAgent,
    CandidateRelevanceBatch,
    CandidateRelevanceDecision,
)
from .coordinator import CoordinatorAgent
from .matcher_judge import MatcherJudgeAgent
from .next_evidence import NextEvidenceAgent
from .selective_reviewer import SelectiveReviewerAgent

__all__ = [
    "AgentResult",
    "CandidateRelevanceAgent",
    "CandidateRelevanceBatch",
    "CandidateRelevanceDecision",
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
