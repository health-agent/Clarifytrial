"""Independent selective-review agent."""

from __future__ import annotations

from .base import ReviewDecision, StructuredAgent


class SelectiveReviewerAgent(StructuredAgent[ReviewDecision]):
    """Review only the flagged conclusion and supplied source excerpts."""

    agent_name = "selective_reviewer"
    prompt_id = "prompts/selective_reviewer.md"
    response_model = ReviewDecision
