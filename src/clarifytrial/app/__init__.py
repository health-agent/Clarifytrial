"""General structured-input application layer."""

from .contracts import GeneralPatientInput, ScreeningSession, StructuredTrialSource
from .challenge import (
    ChallengeRunOptions,
    ChallengeRunOutcome,
    ChallengeTopic,
    ChallengeTopicsInput,
    run_challenge_screening,
)
from .evaluation import run_full_workflow_evaluation
from .runner import GeneralRunOptions, run_general_screening

__all__ = [
    "GeneralPatientInput",
    "GeneralRunOptions",
    "ScreeningSession",
    "StructuredTrialSource",
    "ChallengeRunOptions",
    "ChallengeRunOutcome",
    "ChallengeTopic",
    "ChallengeTopicsInput",
    "run_challenge_screening",
    "run_general_screening",
    "run_full_workflow_evaluation",
]
