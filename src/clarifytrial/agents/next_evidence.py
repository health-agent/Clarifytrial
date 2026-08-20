"""Next-evidence selection agent."""

from __future__ import annotations

from ..contracts import AgentAction
from .base import StructuredAgent


class NextEvidenceAgent(StructuredAgent[AgentAction]):
    """Choose one supplied missing fact and one permitted acquisition path."""

    agent_name = "next_evidence"
    prompt_id = "prompts/next_evidence.md"
    response_model = AgentAction
