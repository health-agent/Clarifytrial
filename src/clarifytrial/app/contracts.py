"""Public JSON contracts for the general structured-input program."""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from ..contracts import (
    ContractModel,
    EvidenceCaptureMethod,
    EvidenceSourceType,
    NextEvidenceRequest,
    PatientState,
    VerificationStatus,
)
from ..interactive.burden_contracts import AcquisitionOption, PatientBurdenInput
from ..workflow import PatientScreeningResult, ScreeningTrial


class StructuredTrialSource(ContractModel):
    """One searchable trial whose criteria have already been structured."""

    trial_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    conditions: list[str] = Field(default_factory=list)
    summary: str = ""
    source_location: str = Field(min_length=1)
    trial: ScreeningTrial

    @model_validator(mode="after")
    def identifiers_match(self) -> "StructuredTrialSource":
        if self.trial.trial_id != self.trial_id:
            raise ValueError("trial.trial_id must match trial_id")
        return self


class GeneralPatientInput(ContractModel):
    """One patient state plus declared missing information and acquisition paths."""

    case_id: str = Field(min_length=1)
    search_conditions: list[str] = Field(min_length=1)
    candidate_count: int = Field(default=5, ge=1)
    patient_state: PatientState
    evidence_requests: list[NextEvidenceRequest] = Field(default_factory=list)
    acquisition_options: list[AcquisitionOption] = Field(default_factory=list)
    patient_burden_input: PatientBurdenInput | None = None

    @model_validator(mode="after")
    def public_identifiers_are_unique(self) -> "GeneralPatientInput":
        fact_ids = [item.fact_id for item in self.evidence_requests]
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("evidence_requests must not repeat fact_id")
        option_ids = [item.option_id for item in self.acquisition_options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("acquisition_options must not repeat option_id")
        return self


class SessionEvent(ContractModel):
    """One answer accepted during an interactive session."""

    step: int = Field(ge=1)
    fact_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    status: str = Field(min_length=1)
    evidence_id: str | None = None
    capture_method: EvidenceCaptureMethod | None = None
    source_type: EvidenceSourceType | None = None
    verification_status: VerificationStatus | None = None
    event_date: str | None = None


class ScreeningSession(ContractModel):
    """Small resumable state file written after every accepted answer."""

    format_version: int = 2
    case_id: str = Field(min_length=1)
    patient_state: PatientState
    revealed_fact_ids: list[str] = Field(default_factory=list)
    unavailable_fact_ids: list[str] = Field(default_factory=list)
    pending_option_id: str | None = None
    patient_approved_option_ids: list[str] = Field(default_factory=list)
    clinician_authorized_option_ids: list[str] = Field(default_factory=list)
    action_count: int = Field(default=0, ge=0)
    events: list[SessionEvent] = Field(default_factory=list)
    completed: bool = False
    result: PatientScreeningResult | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def state_ids_are_unique(self) -> "ScreeningSession":
        fields = (
            "revealed_fact_ids",
            "unavailable_fact_ids",
            "patient_approved_option_ids",
            "clinician_authorized_option_ids",
        )
        for name in fields:
            values = getattr(self, name)
            if len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        overlap = set(self.revealed_fact_ids) & set(self.unavailable_fact_ids)
        if overlap:
            raise ValueError(
                "a fact cannot be both revealed and unavailable: "
                + ", ".join(sorted(overlap))
            )
        return self


__all__ = [
    "GeneralPatientInput",
    "ScreeningSession",
    "SessionEvent",
    "StructuredTrialSource",
]
