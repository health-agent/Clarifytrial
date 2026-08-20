"""Controlled information acquisition for synthetic evaluation cases."""

from .hidden_patient import (
    EnvironmentResponse,
    EnvironmentStatus,
    HiddenFactAnswer,
    HiddenPatientEnvironment,
)
from .tools import (
    PublicFactRequest,
    PublicQuestionCatalog,
    SyntheticInformationTools,
    ToolExecutionResult,
)

__all__ = [
    "EnvironmentResponse",
    "EnvironmentStatus",
    "HiddenFactAnswer",
    "HiddenPatientEnvironment",
    "PublicFactRequest",
    "PublicQuestionCatalog",
    "SyntheticInformationTools",
    "ToolExecutionResult",
]
