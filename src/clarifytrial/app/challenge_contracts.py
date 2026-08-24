"""Input and run contracts for competition-style topic files."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from pydantic import Field, model_validator

from ..contracts import ContractModel
from ..settings import EpisodeSettings
from .runner import GeneralRunOutcome


class ChallengeTopic(ContractModel):
    """One supplied synthetic patient vignette."""

    num: str = Field(min_length=1)
    title: str = Field(min_length=1)


class ChallengeTopicsInput(ContractModel):
    """Competition transport format supplied by the team."""

    topics: list[ChallengeTopic] = Field(min_length=1)

    @model_validator(mode="after")
    def topic_numbers_are_unique(self) -> "ChallengeTopicsInput":
        values = [item.num for item in self.topics]
        if len(values) != len(set(values)):
            raise ValueError("topics must not repeat num")
        return self


@dataclass(frozen=True, slots=True)
class ChallengeRunOptions:
    topics_path: Path
    output_dir: Path
    topic_ids: tuple[str, ...]
    all_topics: bool
    as_of: datetime
    candidate_count: int
    settings: EpisodeSettings
    trial_protocol_cache_dir: Path = Path("runs") / "trial-protocol-cache"
    resume_path: Path | None = None
    retry_unavailable: bool = False
    approve_patient_choice: bool = False
    authorize_clinician: bool = False

    def __post_init__(self) -> None:
        if self.candidate_count < 1:
            raise ValueError("candidate_count must be at least one")
        if self.all_topics == bool(self.topic_ids):
            raise ValueError("choose topic_ids or all_topics")
        if self.all_topics and self.resume_path is not None:
            raise ValueError("resume supports one topic at a time")


@dataclass(frozen=True, slots=True)
class ChallengeRunOutcome:
    topic_ids: tuple[str, ...]
    runs: tuple[GeneralRunOutcome, ...]


__all__ = [
    "ChallengeRunOptions",
    "ChallengeRunOutcome",
    "ChallengeTopic",
    "ChallengeTopicsInput",
]
