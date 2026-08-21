"""Typed inputs, outputs, and stop reasons for multi-trial patient screening."""

from __future__ import annotations

from enum import StrEnum
from typing import Protocol

from pydantic import Field, model_validator

from ..agents import ReviewDecision
from ..contracts import (
    AgentAction,
    ContractModel,
    NextEvidenceRequest,
    PatientState,
    TrialCriterion,
    TrialDecision,
)
from ..environment import ToolExecutionResult
from ..interactive.burden_contracts import (
    AcquisitionDecision,
    AcquisitionOption,
    GuidanceOutput,
    PatientBurdenInput,
)


class InformationTools(Protocol):
    """Minimal boundary for a record, patient-answer, or verification tool."""

    def execute(
        self,
        agent_action: AgentAction,
        patient_state: PatientState,
    ) -> ToolExecutionResult: ...


class PatientScreeningStopReason(StrEnum):
    """Why the multi-trial patient workflow stopped."""

    ALL_TRIALS_RESOLVED = "all_trials_resolved"
    NO_PENDING_INFORMATION = "no_pending_information"
    ACTION_LIMIT = "action_limit"
    AWAITING_PATIENT_CHOICE = "awaiting_patient_choice"
    AWAITING_CLINICIAN_AUTHORIZATION = "awaiting_clinician_authorization"
    DEFERRED = "deferred"
    HUMAN_REVIEW = "human_review"
    TOOL_RETURNED_NO_INFORMATION = "tool_returned_no_information"
    CYCLE_LIMIT = "cycle_limit"


class ScreeningTrial(ContractModel):
    """One candidate trial supplied to the shared patient workflow."""

    trial_id: str = Field(min_length=1)
    criteria: list[TrialCriterion] = Field(min_length=1)

    @model_validator(mode="after")
    def criteria_belong_to_trial(self) -> "ScreeningTrial":
        criterion_ids = [item.criterion_id for item in self.criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("criteria must not repeat criterion_id")
        if any(item.trial_id != self.trial_id for item in self.criteria):
            raise ValueError("every criterion must belong to trial_id")
        if not any(item.required for item in self.criteria):
            raise ValueError("at least one criterion must be required")
        return self


class PatientScreeningCase(ContractModel):
    """Visible inputs for one patient and all supplied candidate trials."""

    case_id: str = Field(min_length=1)
    disease_group: str = Field(default="not_specified", min_length=1)
    trials: list[ScreeningTrial] = Field(min_length=1)
    initial_patient_state: PatientState
    evidence_requests: list[NextEvidenceRequest] = Field(default_factory=list)
    acquisition_options: list[AcquisitionOption] = Field(default_factory=list)
    patient_burden_input: PatientBurdenInput | None = None

    @model_validator(mode="after")
    def references_are_closed(self) -> "PatientScreeningCase":
        trial_ids = [item.trial_id for item in self.trials]
        if len(trial_ids) != len(set(trial_ids)):
            raise ValueError("trials must not repeat trial_id")
        criterion_ids = [
            criterion.criterion_id
            for trial in self.trials
            for criterion in trial.criteria
        ]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("criterion_id must be unique across all trials")
        known_criteria = set(criterion_ids)
        request_ids = [item.fact_id for item in self.evidence_requests]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("evidence_requests must not repeat fact_id")
        for request in self.evidence_requests:
            unknown = set(request.related_criterion_ids) - known_criteria
            if unknown:
                raise ValueError(
                    f"request {request.fact_id!r} refers to unknown criteria: "
                    + ", ".join(sorted(unknown))
                )
        option_ids = [item.option_id for item in self.acquisition_options]
        if len(option_ids) != len(set(option_ids)):
            raise ValueError("acquisition_options must not repeat option_id")
        request_by_id = {item.fact_id: item for item in self.evidence_requests}
        for option in self.acquisition_options:
            request = request_by_id.get(option.fact_id)
            if request is None:
                raise ValueError(
                    f"acquisition option refers to unknown fact_id: {option.fact_id}"
                )
            if option.action not in request.acceptable_actions:
                raise ValueError(
                    f"acquisition option {option.option_id!r} uses a path that the "
                    "request does not allow"
                )
        return self


class PatientScreeningSnapshot(ContractModel):
    """All trial decisions after one assessment or review step."""

    cycle: int = Field(ge=0)
    reason: str = Field(min_length=1)
    decisions: list[TrialDecision] = Field(min_length=1)

    @model_validator(mode="after")
    def trial_ids_are_unique(self) -> "PatientScreeningSnapshot":
        trial_ids = [item.trial_id for item in self.decisions]
        if len(trial_ids) != len(set(trial_ids)):
            raise ValueError("decisions must not repeat trial_id")
        return self


class PatientScreeningActionRecord(ContractModel):
    """One planned and executed information-acquisition action."""

    step: int = Field(ge=1)
    acquisition_decision: AcquisitionDecision
    agent_action: AgentAction
    tool_result: ToolExecutionResult


class PatientScreeningResult(ContractModel):
    """Complete public result of the connected multi-trial workflow."""

    case_id: str
    stop_reason: PatientScreeningStopReason
    final_patient_state: PatientState
    final_decisions: list[TrialDecision]
    decision_history: list[PatientScreeningSnapshot]
    action_history: list[PatientScreeningActionRecord]
    review_history: list[ReviewDecision]
    planned_action: AgentAction | None = None
    guidance: GuidanceOutput
    cycles: int = Field(ge=1)

    @model_validator(mode="after")
    def final_trial_ids_are_unique(self) -> "PatientScreeningResult":
        trial_ids = [item.trial_id for item in self.final_decisions]
        if len(trial_ids) != len(set(trial_ids)):
            raise ValueError("final_decisions must not repeat trial_id")
        return self
