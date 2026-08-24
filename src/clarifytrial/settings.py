"""Runtime settings loaded from a standard TOML file."""

from __future__ import annotations

import tomllib
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ModelSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str
    model_alias: str
    effort: str
    timeout_seconds: int = Field(gt=0)
    format_repair_limit: int = Field(ge=0)


class EpisodeSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_external_actions: int = Field(ge=0)
    max_selective_reviews: int = Field(ge=0)
    max_cycles: int = Field(gt=0)
    use_model_coordinator: bool = False
    batch_trial_judgments: bool = True
    criterion_batch_size: int = Field(default=40, ge=1)
    question_policy: str = Field(
        default="clarifytrial",
        pattern="^(clarifytrial|immediate_coverage|fixed_order)$",
    )


class TraceSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    write_jsonl: bool = True


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: ModelSettings
    episode: EpisodeSettings
    trace: TraceSettings


def load_settings(path: str | Path) -> Settings:
    """Read and validate one TOML settings file."""

    source = Path(path)
    with source.open("rb") as stream:
        return Settings.model_validate(tomllib.load(stream))
