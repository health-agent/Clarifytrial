"""Prepare natural-language patient and trial sources for the workflow."""

from .candidate_search import (
    CandidateSearch,
    InMemoryCandidateSearch,
    TrialGPTCandidateSearch,
)
from .contracts import (
    CandidateSearchHit,
    NaturalScreeningRequest,
    NaturalHiddenFactAnswer,
    PreparedScreeningCase,
    RawPatientRecord,
    TrialProtocolSource,
)
from .pipeline import (
    NaturalScreeningPipeline,
    NaturalScreeningResult,
    summarize_model_usage,
)
from .trial_cache import TrialProtocolCache, TrialProtocolCacheStats
from .synthetic_tools import build_synthetic_information_tools

__all__ = [
    "CandidateSearch",
    "CandidateSearchHit",
    "InMemoryCandidateSearch",
    "NaturalScreeningPipeline",
    "NaturalHiddenFactAnswer",
    "NaturalScreeningRequest",
    "NaturalScreeningResult",
    "PreparedScreeningCase",
    "RawPatientRecord",
    "TrialProtocolSource",
    "TrialGPTCandidateSearch",
    "TrialProtocolCache",
    "TrialProtocolCacheStats",
    "build_synthetic_information_tools",
    "summarize_model_usage",
]
