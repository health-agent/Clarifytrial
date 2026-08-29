"""Judge whether retrieved trials study the patient's searched condition."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .base import StructuredAgent


class CandidateRelevanceDecision(BaseModel):
    """One disease-level relevance decision before eligibility screening."""

    model_config = ConfigDict(extra="forbid")

    trial_id: str = Field(min_length=1)
    relevant: bool
    reason: str = Field(min_length=1)


class CandidateRelevanceBatch(BaseModel):
    """A complete relevance decision for every retrieved candidate."""

    model_config = ConfigDict(extra="forbid")

    decisions: list[CandidateRelevanceDecision] = Field(min_length=1)

    @field_validator("decisions")
    @classmethod
    def trial_ids_are_unique(
        cls,
        value: list[CandidateRelevanceDecision],
    ) -> list[CandidateRelevanceDecision]:
        trial_ids = [item.trial_id for item in value]
        if len(trial_ids) != len(set(trial_ids)):
            raise ValueError("decisions must not repeat trial_id")
        return value


class CandidateRelevanceAgent(StructuredAgent[CandidateRelevanceBatch]):
    """Remove disease mismatches without deciding patient eligibility."""

    agent_name = "candidate_relevance_reviewer"
    prompt_id = "prompts/candidate_relevance_reviewer.md"
    response_model = CandidateRelevanceBatch


__all__ = [
    "CandidateRelevanceAgent",
    "CandidateRelevanceBatch",
    "CandidateRelevanceDecision",
]
