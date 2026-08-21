"""Typed contracts for patient-specific information-acquisition planning.

Eligibility decisions and patient burden are deliberately separate.  These
contracts describe how missing information may be obtained and how that choice
is explained; they never change trial criteria or patient facts.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from ..contracts import ContractModel, NextAction


class AcquisitionMode(StrEnum):
    INTERNAL_RECORD = "internal_record"
    OUTSIDE_RECORD = "outside_record"
    PATIENT_REPORT = "patient_report"
    EXISTING_OFFICIAL_RESULT = "existing_official_result"
    NEW_NONINVASIVE_TEST = "new_noninvasive_test"
    NEW_INVASIVE_OR_TREATMENT_CHANGE = "new_invasive_or_treatment_change"
    CLINICIAN_JUDGMENT = "clinician_judgment"


class DirectCostBand(StrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class PatientInputStatus(StrEnum):
    ABSENT = "absent"
    PARTIAL = "partial"
    COMPLETE = "complete"


class PreferenceMode(StrEnum):
    FASTEST = "fastest"
    LEAST_EXTRA_BURDEN = "least_extra_burden"
    BALANCED = "balanced"


class AcquisitionPolicyId(StrEnum):
    IMPACT_ONLY = "impact_only"
    FIXED_ROUTE_COST = "fixed_route_cost"
    LEAST_EXTRA_BURDEN = "least_extra_burden"
    PATIENT_ADAPTIVE = "patient_adaptive"
    ALL_INFORMATION = "all_information"


class AvailabilityStructure(StrEnum):
    EXISTING_DATA_CENTERED = "existing_data_centered"
    NEW_CONFIRMATION_NEEDED = "new_confirmation_needed"


class ActionStatus(StrEnum):
    RECOMMENDED = "recommended"
    AWAITING_PATIENT_CHOICE = "awaiting_patient_choice"
    AWAITING_CLINICIAN_AUTHORIZATION = "awaiting_clinician_authorization"
    DEFERRED = "deferred"


class PatientStatedLimits(ContractModel):
    """Only limits the patient explicitly supplied are enforceable."""

    max_additional_visits: int | None = Field(default=None, ge=0)
    max_direct_cost_band: DirectCostBand | None = None
    max_physical_burden: int | None = Field(default=None, ge=0, le=3)
    max_medical_risk: int | None = Field(default=None, ge=0, le=3)
    allow_new_tests: bool | None = None
    allow_treatment_change: bool | None = None
    explicitly_no_limits: bool = False

    @model_validator(mode="after")
    def declaration_is_unambiguous(self) -> "PatientStatedLimits":
        values = (
            self.max_additional_visits,
            self.max_direct_cost_band,
            self.max_physical_burden,
            self.max_medical_risk,
            self.allow_new_tests,
            self.allow_treatment_change,
        )
        if self.explicitly_no_limits and any(item is not None for item in values):
            raise ValueError("explicitly_no_limits cannot be combined with limits")
        if not self.explicitly_no_limits and all(item is None for item in values):
            raise ValueError("stated limits need one explicit value")
        return self


class PatientBurdenInput(ContractModel):
    """Optional values entered by a patient or research fixture."""

    time_urgency_0_to_3: int | None = Field(default=None, ge=0, le=3)
    fatigue_or_mobility_limit_0_to_3: int | None = Field(
        default=None, ge=0, le=3
    )
    travel_constraint_0_to_3: int | None = Field(default=None, ge=0, le=3)
    cost_sensitivity_0_to_3: int | None = Field(default=None, ge=0, le=3)
    procedure_aversion_0_to_3: int | None = Field(default=None, ge=0, le=3)
    treatment_change_aversion_0_to_3: int | None = Field(
        default=None, ge=0, le=3
    )
    preference_mode: PreferenceMode | None = None
    stated_limits: PatientStatedLimits | None = None


class PatientBurdenProfile(ContractModel):
    profile_id: str = Field(min_length=1)
    input_status: PatientInputStatus
    time_urgency_0_to_3: int | None = Field(default=None, ge=0, le=3)
    fatigue_or_mobility_limit_0_to_3: int | None = Field(
        default=None, ge=0, le=3
    )
    travel_constraint_0_to_3: int | None = Field(default=None, ge=0, le=3)
    cost_sensitivity_0_to_3: int | None = Field(default=None, ge=0, le=3)
    procedure_aversion_0_to_3: int | None = Field(default=None, ge=0, le=3)
    treatment_change_aversion_0_to_3: int | None = Field(
        default=None, ge=0, le=3
    )
    preference_mode: PreferenceMode = PreferenceMode.BALANCED
    stated_limits: PatientStatedLimits | None = None
    defaulted_fields: list[str] = Field(default_factory=list)


class AcquisitionOption(ContractModel):
    option_id: str = Field(min_length=1)
    fact_id: str = Field(min_length=1)
    action: NextAction
    acquisition_mode: AcquisitionMode
    available_now: bool
    expected_delay_hours: float | None = Field(default=None, ge=0)
    visit_required: bool | None = None
    direct_cost_band: DirectCostBand = DirectCostBand.UNKNOWN
    physical_burden_0_to_3: int | None = Field(default=None, ge=0, le=3)
    emotional_burden_0_to_3: int | None = Field(default=None, ge=0, le=3)
    medical_risk_0_to_3: int | None = Field(default=None, ge=0, le=3)
    treatment_disruption_0_to_3: int | None = Field(default=None, ge=0, le=3)
    already_planned_in_care: bool = False
    new_test_required: bool = False
    requires_patient_choice: bool = False
    requires_clinician_authorization: bool = False
    source_note: str = Field(min_length=1)

    @model_validator(mode="after")
    def safety_fields_match_mode(self) -> "AcquisitionOption":
        new_modes = {
            AcquisitionMode.NEW_NONINVASIVE_TEST,
            AcquisitionMode.NEW_INVASIVE_OR_TREATMENT_CHANGE,
        }
        if (
            self.acquisition_mode is AcquisitionMode.NEW_NONINVASIVE_TEST
            and not self.new_test_required
        ):
            raise ValueError("new noninvasive tests need new_test_required=true")
        if self.new_test_required and self.acquisition_mode not in new_modes:
            raise ValueError("new_test_required is only valid for new test modes")
        if self.acquisition_mode in new_modes and not (
            self.requires_patient_choice
            and self.requires_clinician_authorization
        ):
            raise ValueError("new tests require patient choice and clinician authorization")
        if (
            self.acquisition_mode
            is AcquisitionMode.NEW_INVASIVE_OR_TREATMENT_CHANGE
            and (self.treatment_disruption_0_to_3 or 0) == 0
        ):
            raise ValueError("invasive or treatment-change options need disruption")
        return self


class RemovedOption(ContractModel):
    option_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class DecisionTrace(ContractModel):
    considered_option_ids: list[str]
    removed_options: list[RemovedOption]
    applied_ordering_rule: list[str]
    first_decisive_difference: str | None = None
    unresolved_unknown_fields: list[str] = Field(default_factory=list)


class AcquisitionDecision(ContractModel):
    policy_id: AcquisitionPolicyId
    selected_option: AcquisitionOption | None = None
    alternative_options: list[AcquisitionOption] = Field(default_factory=list)
    action_status: ActionStatus
    selection_reason: str = Field(min_length=1)
    decision_trace: DecisionTrace


class TrialGroups(ContractModel):
    confirmed_trial_ids: list[str]
    pending_trial_ids: list[str]
    removed_trial_ids: list[str]


class OutcomePreviewBranch(ContractModel):
    affected_trial_ids: list[str]
    message: str = Field(min_length=1)


class OutcomePreview(ContractModel):
    if_satisfies: OutcomePreviewBranch
    if_violates: OutcomePreviewBranch
    if_unavailable: OutcomePreviewBranch


class PatientGuidance(ContractModel):
    fact_id: str | None = None
    affected_trial_ids: list[str] = Field(default_factory=list)
    current_result: list[str]
    next_information: str
    recommended_route: str
    existing_or_new: str
    reason: str
    expected_burden: list[str]
    applied_patient_settings: list[str]
    choices_and_alternatives: list[str]
    outcome_preview: OutcomePreview | None = None
    medical_disclaimer: str


class DetailedSelectedOption(ContractModel):
    fact_id: str
    option_id: str
    action: NextAction
    acquisition_mode: AcquisitionMode
    affected_trial_ids: list[str]
    related_criterion_ids: list[str]
    existing_or_new: str
    expected_delay_hours: float | None = None
    burden_fields: dict[str, Any]
    requires_patient_choice: bool
    requires_clinician_authorization: bool
    action_status: ActionStatus
    selection_reason: str


class DetailedAlternative(ContractModel):
    option_id: str
    acquisition_mode: AcquisitionMode
    difference_from_selected: str
    not_selected_reason: str


class GuidanceOutput(ContractModel):
    case_id: str
    generated_at: str
    burden_policy_version: str
    patient_input_status: PatientInputStatus
    preference_mode: PreferenceMode
    defaulted_fields: list[str]
    trial_groups: TrialGroups
    selected_option: DetailedSelectedOption | None = None
    alternatives: list[DetailedAlternative]
    outcome_preview: OutcomePreview | None = None
    evidence_refs: list[str]
    stop_reason: str | None = None
    decision_trace: DecisionTrace
    patient_message: PatientGuidance
    medical_disclaimer: str


class BurdenRunMetrics(ContractModel):
    trial_status_recovery: float = Field(ge=0, le=1)
    burden_feasible_trial_status_recovery: float = Field(ge=0, le=1)
    candidate_status_recovery: float = Field(ge=0, le=1)
    confirmation_status_recovery: float = Field(ge=0, le=1)
    action_count: int = Field(ge=0)
    new_test_count: int = Field(ge=0)
    additional_visit_count: int = Field(ge=0)
    cumulative_delay_hours: float = Field(ge=0)
    cumulative_cost_rank: int = Field(ge=0)
    cumulative_physical_burden: int = Field(ge=0)
    cumulative_emotional_burden: int = Field(ge=0)
    cumulative_medical_risk: int = Field(ge=0)
    cumulative_treatment_disruption: int = Field(ge=0)
    unknown_cost_count: int = Field(ge=0)
    unknown_delay_count: int = Field(ge=0)
    unknown_burden_field_count: int = Field(ge=0)
    explicit_limit_violations: int = Field(ge=0)
    unauthorized_auto_actions: int = Field(ge=0)
    dominated_option_selections: int = Field(ge=0)
    authorization_required_actions: int = Field(ge=0)


class BurdenActionRecord(ContractModel):
    step: int = Field(ge=1)
    decision: AcquisitionDecision
    synthetic_authorization_granted: bool
    answer_released: bool


class BurdenPolicyRun(ContractModel):
    case_id: str
    base_profile_id: str
    split: str
    disease_group: str
    mask_id: str
    patient_profile_id: str
    availability_structure: AvailabilityStructure
    policy_id: AcquisitionPolicyId
    selected_option_ids: list[str]
    selected_fact_ids: list[str]
    action_history: list[BurdenActionRecord]
    final_trial_groups: TrialGroups
    guidance: GuidanceOutput
    metrics: BurdenRunMetrics
