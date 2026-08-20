"""Search records preserve trial, criterion, text, and source identifiers."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class SearchDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str
    trial_id: str
    criterion_id: str
    criterion_type: str
    raw_text: str = Field(min_length=1)
    source_location: str = Field(min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchHit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: int = Field(ge=1)
    score: float
    document: SearchDocument
