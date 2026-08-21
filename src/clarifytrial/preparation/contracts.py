"""Typed natural-language inputs and model drafts used before screening."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import Field, model_validator

from ..contracts import (
    ContractModel,
    CriterionKind,
    EvidenceFact,
    EvidenceRequirement,
    EvidenceSourceType,
    NextAction,
    NumericConstraint,
    PatientState,
    VerificationStatus,
)
from ..interactive.burden_contracts import (
    AcquisitionMode,
    DirectCostBand,
    PatientBurdenInput,
)
from ..workflow import PatientScreeningCase


class RawPatientRecord(ContractModel):
    """One natural-language patient record with observable source metadata."""

    patient_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    recorded_at: datetime
    as_of: datetime
    source_type: EvidenceSourceType
    verification_status: VerificationStatus


class PatientFactDraft(ContractModel):
    """A model-proposed fact with a source quote and optional location hint."""

    fact_key: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    source_quote: str = Field(min_length=1)
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, gt=0)
    event_date: date | None = None
    concept: str | None = Field(default=None, min_length=1)
    value: float | None = Field(default=None, allow_inf_nan=False)
    unit: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def offsets_and_structured_value_are_complete(self) -> "PatientFactDraft":
        if (self.start_char is None) != (self.end_char is None):
            raise ValueError("start_char and end_char must be provided together")
        if (
            self.start_char is not None
            and self.end_char is not None
            and self.end_char <= self.start_char
        ):
            raise ValueError("end_char must be greater than start_char")
        provided = (
            self.concept is not None,
            self.value is not None,
            self.unit is not None,
        )
        if any(provided) and not all(provided):
            raise ValueError("concept, value, and unit must be provided together")
        return self


class SearchConditionDraft(ContractModel):
    """A normalized search condition anchored to patient-record source text."""

    condition: str = Field(min_length=1)
    source_quote: str = Field(min_length=1)
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def offsets_are_ordered(self) -> "SearchConditionDraft":
        if (self.start_char is None) != (self.end_char is None):
            raise ValueError("start_char and end_char must be provided together")
        if (
            self.start_char is not None
            and self.end_char is not None
            and self.end_char <= self.start_char
        ):
            raise ValueError("end_char must be greater than start_char")
        return self


class PatientRecordDraft(ContractModel):
    """Search terms and patient facts proposed from one supplied record."""

    search_conditions: list[SearchConditionDraft] = Field(min_length=1)
    facts: list[PatientFactDraft] = Field(min_length=1)

    @model_validator(mode="after")
    def fact_keys_and_search_conditions_are_unique(self) -> "PatientRecordDraft":
        fact_keys = [item.fact_key for item in self.facts]
        if len(fact_keys) != len(set(fact_keys)):
            raise ValueError("facts must not repeat fact_key")
        conditions = [item.condition for item in self.search_conditions]
        if len(conditions) != len(set(conditions)):
            raise ValueError("search_conditions must not contain duplicates")
        return self


class TrialProtocolSource(ContractModel):
    """Raw candidate-trial text returned by the common search layer."""

    trial_id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    conditions: list[str] = Field(default_factory=list)
    summary: str = ""
    eligibility_text: str = Field(min_length=1)
    source_location: str = Field(min_length=1)


class CandidateSearchHit(ContractModel):
    """One ranked candidate and the retrieval score used to include it."""

    rank: int = Field(ge=1)
    score: float
    retrieval_method: str = Field(min_length=1)
    source: TrialProtocolSource


class InformationNeedDraft(ContractModel):
    """One fact that may be missing when a criterion is judged."""

    fact_key: str = Field(min_length=1)
    description: str = Field(min_length=1)
    acceptable_actions: list[NextAction] = Field(min_length=1)

    @model_validator(mode="after")
    def actions_can_obtain_or_defer_information(self) -> "InformationNeedDraft":
        if NextAction.NONE in self.acceptable_actions:
            raise ValueError("NONE cannot obtain a missing fact")
        if len(self.acceptable_actions) != len(set(self.acceptable_actions)):
            raise ValueError("acceptable_actions must not contain duplicates")
        return self


class TrialCriterionDraft(ContractModel):
    """A criterion proposed from a quoted part of a trial source."""

    kind: CriterionKind
    statement: str = Field(min_length=1)
    source_quote: str = Field(min_length=1)
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, gt=0)
    numeric_constraint: NumericConstraint | None = None
    evidence_requirement: EvidenceRequirement | None = None
    information_needs: list[InformationNeedDraft] = Field(default_factory=list)

    @model_validator(mode="after")
    def offsets_are_ordered(self) -> "TrialCriterionDraft":
        if (self.start_char is None) != (self.end_char is None):
            raise ValueError("start_char and end_char must be provided together")
        if (
            self.start_char is not None
            and self.end_char is not None
            and self.end_char <= self.start_char
        ):
            raise ValueError("end_char must be greater than start_char")
        return self


class TrialProtocolDraft(ContractModel):
    """All normalized criteria proposed for one supplied candidate trial."""

    criteria: list[TrialCriterionDraft] = Field(min_length=1)


class AcquisitionPathInput(ContractModel):
    """Availability and burden supplied for one fact key, never inferred by LLM."""

    fact_key: str = Field(min_length=1)
    fact_description: str = Field(min_length=1)
    path_key: str = Field(min_length=1)
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


class NaturalScreeningRequest(ContractModel):
    """Natural patient input plus declared acquisition paths for one run."""

    case_id: str = Field(min_length=1)
    patient_record: RawPatientRecord
    candidate_count: int = Field(default=5, ge=1)
    patient_burden_input: PatientBurdenInput | None = None
    acquisition_paths: list[AcquisitionPathInput] = Field(default_factory=list)

    @model_validator(mode="after")
    def acquisition_path_keys_and_descriptions_are_consistent(
        self,
    ) -> "NaturalScreeningRequest":
        path_keys = [
            (item.fact_key, item.path_key) for item in self.acquisition_paths
        ]
        if len(path_keys) != len(set(path_keys)):
            raise ValueError("acquisition paths must not repeat fact_key and path_key")
        descriptions: dict[str, str] = {}
        for item in self.acquisition_paths:
            previous = descriptions.setdefault(
                item.fact_key,
                item.fact_description,
            )
            if previous != item.fact_description:
                raise ValueError(
                    "acquisition paths for one fact_key need one fact_description"
                )
        return self


class NaturalHiddenFactAnswer(ContractModel):
    """Synthetic answer keyed by fact_key and kept outside the model request."""

    fact_key: str = Field(min_length=1)
    access_path: NextAction
    evidence: EvidenceFact


class PreparedScreeningCase(ContractModel):
    """Inspectable bridge from natural sources to the structured workflow."""

    request_case_id: str
    patient_state: PatientState
    search_conditions: list[str]
    candidate_hits: list[CandidateSearchHit]
    fact_id_by_key: dict[str, str]
    screening_case: PatientScreeningCase


class RoleTokenUsage(ContractModel):
    """Provider-reported token counters summed for one model role."""

    call_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    thinking_tokens: int = Field(ge=0)
    cache_creation_input_tokens: int = Field(ge=0)
    cache_read_input_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    calls_with_provider_total: int = Field(ge=0)


class NaturalScreeningUsage(ContractModel):
    """All model calls in preparation and repeated screening, split by role."""

    call_count: int = Field(ge=0)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    thinking_tokens: int = Field(ge=0)
    cache_creation_input_tokens: int = Field(ge=0)
    cache_read_input_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    calls_with_provider_total: int = Field(ge=0)
    by_role: dict[str, RoleTokenUsage]
