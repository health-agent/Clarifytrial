"""Coordinator agent: select one permitted next workflow step."""

from __future__ import annotations

from .base import CoordinatorDecision, StructuredAgent


class CoordinatorAgent(StructuredAgent[CoordinatorDecision]):
    """Route from structured state summaries without re-judging evidence."""

    agent_name = "coordinator"
    prompt_id = "prompts/coordinator.md"
    response_model = CoordinatorDecision
