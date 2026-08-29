"""Public data contracts used by every ClarifyTrial component.

The language model may propose structured values, but these models define the
only values accepted by the workflow.  Every criterion judgment carries both
the patient evidence identifiers and the source location of the trial text.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationInfo,
    field_validator,
    model_validator,
)


class CandidateStatus(str, Enum):
    """Whether a patient should remain under consideration for a trial."""

    RETAIN = "retain"
    REMOVE = "remove"
    UNCERTAIN = "uncertain"


class ConfirmationStatus(str, Enum):
    """What the currently available evidence can establish."""

    CONFIRMED = "confirmed"
    NOT_CONFIRMED = "not_confirmed"
    INELIGIBLE = "ineligible"
    UNCERTAIN = "uncertain"


class NextAction(str, Enum):
    """Permitted ways to obtain or defer missing information."""

    NONE = "NONE"
    LOOKUP_RECORD = "LOOKUP_RECORD"
    ASK_PATIENT = "ASK_PATIENT"
    REQUEST_VERIFICATION = "REQUEST_VERIFICATION"
    DEFER = "DEFER"


class ClinicalStatus(str, Enum):
    """How the known patient facts relate to one normalized criterion."""

    SUPPORTS = "supports"
    VIOLATES = "violates"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class EvidenceSufficiency(str, Enum):
    """Whether the cited evidence is adequate at the current screening stage."""

    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"
    CONFLICTING = "conflicting"


class EvidenceSourceType(str, Enum):
    """Observable origin of a patient fact."""

    MEDICAL_RECORD = "medical_record"
    PATIENT_REPORT = "patient_report"
    OFFICIAL_VERIFICATION = "official_verification"
    SYNTHETIC_CASE = "synthetic_case"


class VerificationStatus(str, Enum):
    """How directly a fact has been verified."""

    VERIFIED = "verified"
    REPORTED = "reported"
    PENDING = "pending"
    CONFLICTING = "conflicting"


class EvidenceCaptureMethod(str, Enum):
    """How an evidence item entered the observable patient state."""

    INTERACTIVE_TEXT = "interactive_text"
    INTERACTIVE_JSON = "interactive_json"
    IMPORTED_JSON_FILE = "imported_json_file"
    SYNTHETIC_ENVIRONMENT = "synthetic_environment"


class CriterionKind(str, Enum):
    """Original role of a criterion in the trial protocol."""

    INCLUSION = "inclusion"
    EXCLUSION = "exclusion"


class CriterionLogicOperator(str, Enum):
    """How criterion results are combined for one eligibility route."""

    CRITERION = "criterion"
    ALL = "all"
    ANY = "any"
    AT_LEAST = "at_least"


class CriterionLogicStatus(str, Enum):
    """Four-valued result of evaluating a criterion logic tree."""

    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNRESOLVED = "unresolved"
    CONFLICTING = "conflicting"


class ComparisonOperator(str, Enum):
    """Numeric comparisons supported by the transparent rule checker."""

    GT = "gt"
    GTE = "gte"
    LT = "lt"
    LTE = "lte"
    EQ = "eq"


class BoundaryPosition(str, Enum):
    """Where the current value lies relative to one criterion threshold."""

    BELOW = "below"
    EQUAL = "equal"
    ABOVE = "above"


class ReviewFlag(str, Enum):
    """Structured defects that may require an independent evidence review."""

    MISSING_EVIDENCE = "missing_evidence"
    EVIDENCE_CONFLICT = "evidence_conflict"
    CODE_MODEL_MISMATCH = "code_model_mismatch"
    CRITERION_SOURCE_MISMATCH = "criterion_source_mismatch"
    UNSUPPORTED_RATIONALE = "unsupported_rationale"
    OUT_OF_SCOPE_FACT = "out_of_scope_fact"


class ReviewReason(str, Enum):
    """Reasons calculated by the selective-review rule."""

    EXPLICIT_FLAG = "explicit_flag"
    MISSING_EVIDENCE = "missing_evidence"
    EVIDENCE_CONFLICT = "evidence_conflict"
    CODE_MODEL_MISMATCH = "code_model_mismatch"
    CRITERION_SOURCE_MISMATCH = "criterion_source_mismatch"
    DECISIVE_RESULT_EVIDENCE_DEFECT = "decisive_result_evidence_defect"


class ContractModel(BaseModel):
    """Strict base class for reproducible JSON contracts."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class EvidenceInputProvenance(ContractModel):
    """Audit details kept separate from the clinical source claim itself."""

    capture_method: EvidenceCaptureMethod
    requested_action: NextAction | None = None
    source_type_declared: bool = False
    source_location_declared: bool = False
    verification_status_declared: bool = False
    event_date_declared: bool = False
    recorded_date_declared: bool = False


def _require_unique(values: list[str], field_name: str) -> list[str]:
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicate identifiers")
    return values


class EvidenceFact(ContractModel):
    """One patient fact with an inspectable origin and date."""

    evidence_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    source_type: EvidenceSourceType
    source_location: str = Field(min_length=1)
    event_date: date | None = None
    recorded_date: date | None = None
    verification_status: VerificationStatus
    concept: str | None = Field(default=None, min_length=1)
    value: float | None = Field(default=None, allow_inf_nan=False)
    unit: str | None = Field(default=None, min_length=1)
    input_provenance: EvidenceInputProvenance | None = None

    @model_validator(mode="after")
    def structured_numeric_fields_form_one_value(self) -> Self:
        provided = (
            self.concept is not None,
            self.value is not None,
            self.unit is not None,
        )
        if any(provided) and not all(provided):
            raise ValueError("concept, value, and unit must be provided together")
        return self


class PatientState(ContractModel):
    """The complete visible patient state at one decision time."""

    patient_id: str = Field(min_length=1)
    as_of: datetime
    facts: list[EvidenceFact] = Field(default_factory=list)

    @model_validator(mode="after")
    def evidence_ids_are_unique(self) -> Self:
        _require_unique([fact.evidence_id for fact in self.facts], "facts.evidence_id")
        return self


class NumericConstraint(ContractModel):
    """A numeric eligibility condition that requires no free-text parsing."""

    concept: str = Field(min_length=1)
    operator: ComparisonOperator
    threshold: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1)


class CriterionLogic(ContractModel):
    """Nested AND, OR, or N-of-M logic over criterion identifiers.

    A leaf uses ``operator=criterion`` and names exactly one criterion.  Group
    nodes contain child expressions.  Labels are optional human-readable route
    names such as a study arm or an alternative eligibility pathway.
    """

    operator: CriterionLogicOperator
    criterion_id: str | None = Field(default=None, min_length=1)
    children: list["CriterionLogic"] = Field(default_factory=list)
    minimum_required: int | None = Field(default=None, ge=1)
    label: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def node_shape_matches_operator(self) -> Self:
        if self.operator is CriterionLogicOperator.CRITERION:
            if self.criterion_id is None:
                raise ValueError("criterion logic leaf needs criterion_id")
            if self.children:
                raise ValueError("criterion logic leaf cannot have children")
            if self.minimum_required is not None:
                raise ValueError("criterion logic leaf cannot set minimum_required")
            return self

        if self.criterion_id is not None:
            raise ValueError("criterion logic group cannot set criterion_id")
        if not self.children:
            raise ValueError("criterion logic group needs at least one child")
        if self.operator is CriterionLogicOperator.AT_LEAST:
            if self.minimum_required is None:
                raise ValueError("at_least logic needs minimum_required")
            if self.minimum_required > len(self.children):
                raise ValueError("minimum_required exceeds child count")
        elif self.minimum_required is not None:
            raise ValueError("only at_least logic can set minimum_required")
        return self

    def referenced_criterion_ids(self) -> set[str]:
        if self.operator is CriterionLogicOperator.CRITERION:
            assert self.criterion_id is not None
            return {self.criterion_id}
        return {
            criterion_id
            for child in self.children
            for criterion_id in child.referenced_criterion_ids()
        }


class CriterionLogicEvaluation(ContractModel):
    """Inspectable result for every node of a criterion logic tree."""

    operator: CriterionLogicOperator
    status: CriterionLogicStatus
    criterion_id: str | None = Field(default=None, min_length=1)
    label: str | None = Field(default=None, min_length=1)
    minimum_required: int | None = Field(default=None, ge=1)
    children: list["CriterionLogicEvaluation"] = Field(default_factory=list)


class EvidenceRequirement(ContractModel):
    """Observable conditions that make a patient fact usable for confirmation."""

    max_age_days: int | None = Field(default=None, ge=0)
    allowed_source_types: list[EvidenceSourceType] | None = Field(default=None, min_length=1)
    allowed_verification_statuses: list[VerificationStatus] | None = Field(
        default=None,
        min_length=1,
    )

    @field_validator("allowed_source_types", "allowed_verification_statuses")
    @classmethod
    def allowed_values_are_unique(
        cls,
        value: list[EvidenceSourceType] | list[VerificationStatus] | None,
        info: ValidationInfo,
    ) -> list[EvidenceSourceType] | list[VerificationStatus] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError(f"{info.field_name} must not contain duplicates")
        return value


class TrialCriterion(ContractModel):
    """One normalized trial criterion tied to its protocol text."""

    criterion_id: str = Field(min_length=1)
    trial_id: str = Field(min_length=1)
    kind: CriterionKind
    statement: str = Field(min_length=1)
    source_location: str = Field(min_length=1)
    required: bool = True
    numeric_constraint: NumericConstraint | None = None
    evidence_requirement: EvidenceRequirement | None = None


class CriterionAssessment(ContractModel):
    """A model judgment that remains traceable to both sides of the evidence."""

    criterion_id: str = Field(min_length=1)
    criterion_source_location: str = Field(min_length=1)
    clinical_status: ClinicalStatus
    evidence_sufficiency: EvidenceSufficiency
    evidence_ids: list[str] = Field(default_factory=list)
    missing_information_ids: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    review_flags: list[ReviewFlag] = Field(default_factory=list)

    @field_validator("evidence_ids", "missing_information_ids")
    @classmethod
    def identifiers_are_unique(
        cls, value: list[str], info: ValidationInfo
    ) -> list[str]:
        field_name = info.field_name
        return _require_unique(value, field_name)

    @field_validator("review_flags")
    @classmethod
    def review_flags_are_unique(cls, value: list[ReviewFlag]) -> list[ReviewFlag]:
        if len(value) != len(set(value)):
            raise ValueError("review_flags must not contain duplicates")
        return value


class NextEvidenceRequest(ContractModel):
    """A missing fact and the allowed routes for obtaining it."""

    fact_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    related_criterion_ids: list[str] = Field(min_length=1)
    acceptable_actions: list[NextAction] = Field(min_length=1)
    reason: str = Field(min_length=1)

    @field_validator("related_criterion_ids")
    @classmethod
    def criterion_ids_are_unique(cls, value: list[str]) -> list[str]:
        return _require_unique(value, "related_criterion_ids")

    @field_validator("acceptable_actions")
    @classmethod
    def actions_are_meaningful_and_unique(
        cls, value: list[NextAction]
    ) -> list[NextAction]:
        if NextAction.NONE in value:
            raise ValueError("NONE cannot obtain a requested fact")
        if len(value) != len(set(value)):
            raise ValueError("acceptable_actions must not contain duplicates")
        return value


class MissingInformationSummary(ContractModel):
    """One unresolved fact shown in a patient-readable trial summary."""

    fact_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    confirmation_methods: list[str] = Field(min_length=1)


class TrialSearchRank(ContractModel):
    """The candidate-search position preserved through the screening workflow."""

    trial_id: str = Field(min_length=1)
    rank: int = Field(ge=1)
    score: float = Field(allow_inf_nan=False)
    retrieval_method: str = Field(min_length=1)


class TrialRecommendationSummary(ContractModel):
    """One trial in either recommendation view."""

    trial_id: str = Field(min_length=1)
    status_label: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    missing_information: list[MissingInformationSummary] = Field(default_factory=list)
    recommendation_rank: int | None = Field(default=None, ge=1)
    search_rank: int | None = Field(default=None, ge=1)
    ranking_explanation: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def missing_fact_ids_are_unique(self) -> Self:
        _require_unique(
            [item.fact_id for item in self.missing_information],
            "missing_information.fact_id",
        )
        return self


class RecommendationList(ContractModel):
    """A recommendation list with its inclusion rule stated in plain language."""

    title: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    trials: list[TrialRecommendationSummary] = Field(default_factory=list)

    @model_validator(mode="after")
    def trial_ids_are_unique(self) -> Self:
        _require_unique([item.trial_id for item in self.trials], "trials.trial_id")
        return self


class RecommendationViews(ContractModel):
    """The strict current-evidence list and the broader review list."""

    current_evidence: RecommendationList
    broader_review: RecommendationList

    @model_validator(mode="after")
    def current_list_is_contained_in_broader_list(self) -> Self:
        current = {item.trial_id for item in self.current_evidence.trials}
        broader = {item.trial_id for item in self.broader_review.trials}
        if not current.issubset(broader):
            raise ValueError("broader_review must contain every current_evidence trial")
        return self


class AgentAction(ContractModel):
    """One executable next action selected from the public action set."""

    action: NextAction
    target_fact_id: str | None = None
    related_criterion_ids: list[str] = Field(default_factory=list)
    reason: str = Field(min_length=1)
    message: str | None = None

    @field_validator("related_criterion_ids")
    @classmethod
    def criterion_ids_are_unique(cls, value: list[str]) -> list[str]:
        return _require_unique(value, "related_criterion_ids")

    @model_validator(mode="after")
    def action_has_required_references(self) -> Self:
        if self.action is NextAction.NONE:
            if self.target_fact_id is not None or self.message is not None:
                raise ValueError("NONE cannot target a fact or carry a request message")
            return self

        if not self.target_fact_id:
            raise ValueError("an action other than NONE must target one missing fact")
        if not self.related_criterion_ids:
            raise ValueError("an action other than NONE must name a related criterion")
        if self.action in {
            NextAction.ASK_PATIENT,
            NextAction.REQUEST_VERIFICATION,
        } and not self.message:
            raise ValueError("patient questions and verification requests need a message")
        return self


class TrialDecision(ContractModel):
    """The two trial-level judgments plus evidence, pending facts, and next action."""

    trial_id: str = Field(min_length=1)
    candidate_status: CandidateStatus
    confirmation_status: ConfirmationStatus
    criterion_assessments: list[CriterionAssessment]
    pending_information: list[NextEvidenceRequest] = Field(default_factory=list)
    next_action: AgentAction
    review_required: bool = False
    review_reasons: list[ReviewReason] = Field(default_factory=list)
    logic_evaluation: CriterionLogicEvaluation | None = None

    @field_validator("criterion_assessments")
    @classmethod
    def assessments_are_unique(
        cls, value: list[CriterionAssessment]
    ) -> list[CriterionAssessment]:
        _require_unique(
            [assessment.criterion_id for assessment in value],
            "criterion_assessments.criterion_id",
        )
        return value

    @field_validator("pending_information")
    @classmethod
    def pending_fact_ids_are_unique(
        cls, value: list[NextEvidenceRequest]
    ) -> list[NextEvidenceRequest]:
        _require_unique(
            [request.fact_id for request in value],
            "pending_information.fact_id",
        )
        return value

    @field_validator("review_reasons")
    @classmethod
    def review_reasons_are_unique(cls, value: list[ReviewReason]) -> list[ReviewReason]:
        if len(value) != len(set(value)):
            raise ValueError("review_reasons must not contain duplicates")
        return value

    @model_validator(mode="after")
    def review_state_is_consistent(self) -> Self:
        if self.review_required and not self.review_reasons:
            raise ValueError("review_required needs at least one review reason")
        if not self.review_required and self.review_reasons:
            raise ValueError("review reasons require review_required=true")
        return self


class CriterionBoundaryDifference(ContractModel):
    """Arithmetic difference for one decisive numeric or temporal criterion."""

    trial_id: str = Field(min_length=1)
    criterion_id: str = Field(min_length=1)
    criterion_kind: CriterionKind
    criterion_statement: str = Field(min_length=1)
    criterion_source_location: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    current_value: float = Field(allow_inf_nan=False)
    threshold: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1)
    operator: ComparisonOperator
    position: BoundaryPosition
    difference_from_threshold: float = Field(allow_inf_nan=False)
    absolute_difference: float = Field(ge=0, allow_inf_nan=False)
    explanation: str = Field(min_length=1)
    comparison_limit: str = Field(
        default=(
            "이 차이는 같은 조건 안에서만 해석합니다. 단위와 의미가 다른 조건끼리 "
            "가까운 정도를 비교하지 않습니다."
        ),
        min_length=1,
    )


class CriterionChangeKind(str, Enum):
    """What kind of later change or check a violated condition permits."""

    RECHECKABLE_MEASUREMENT = "recheckable_measurement"
    ELAPSED_TIME = "elapsed_time"
    FIXED_OR_HISTORICAL = "fixed_or_historical"
    CLINICAL_STATE_OR_PROCEDURE = "clinical_state_or_procedure"
    UNCLEAR = "unclear"


class ReconsiderationPathStatus(str, Enum):
    """Whether one logical eligibility route is worth checking again."""

    CAN_RECHECK = "can_recheck"
    NEEDS_CLINICAL_REVIEW = "needs_clinical_review"
    NO_CURRENT_PATH = "no_current_path"


class CriterionChangeDetail(ContractModel):
    """Plain-language feasibility note for one violated criterion."""

    criterion_id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    kind: CriterionChangeKind
    explanation: str = Field(min_length=1)


class CriterionChangePath(ContractModel):
    """One smallest known set of changes for an eligibility route."""

    criterion_ids: list[str] = Field(min_length=1)
    criterion_statements: list[str] = Field(min_length=1)
    reconsideration_status: ReconsiderationPathStatus = (
        ReconsiderationPathStatus.NEEDS_CLINICAL_REVIEW
    )
    change_details: list[CriterionChangeDetail] = Field(default_factory=list)
    still_unconfirmed_criterion_ids: list[str] = Field(default_factory=list)
    still_unconfirmed_statements: list[str] = Field(default_factory=list)
    explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def criterion_lists_match(self) -> Self:
        _require_unique(self.criterion_ids, "criterion_ids")
        _require_unique(
            self.still_unconfirmed_criterion_ids,
            "still_unconfirmed_criterion_ids",
        )
        if len(self.criterion_ids) != len(self.criterion_statements):
            raise ValueError("criterion_ids and criterion_statements must match")
        if self.change_details:
            if [item.criterion_id for item in self.change_details] != self.criterion_ids:
                raise ValueError("change_details must follow criterion_ids")
            if [item.statement for item in self.change_details] != self.criterion_statements:
                raise ValueError("change_details must follow criterion_statements")
        if len(self.still_unconfirmed_criterion_ids) != len(
            self.still_unconfirmed_statements
        ):
            raise ValueError(
                "still_unconfirmed criterion identifiers and statements must match"
            )
        return self


class CriterionRecheckDate(ContractModel):
    """A deterministic date when an elapsed-time criterion can be checked again."""

    trial_id: str = Field(min_length=1)
    criterion_id: str = Field(min_length=1)
    evidence_id: str = Field(min_length=1)
    current_elapsed: float = Field(ge=0, allow_inf_nan=False)
    required_elapsed: float = Field(ge=0, allow_inf_nan=False)
    unit: str = Field(min_length=1)
    days_remaining: int = Field(ge=1)
    recheck_date: date
    assumption: str = Field(min_length=1)
    explanation: str = Field(min_length=1)


class TrialReconsiderationSummary(ContractModel):
    """Inspectable ways an ineligible trial could become worth checking again."""

    trial_id: str = Field(min_length=1)
    minimum_change_count: int = Field(ge=1)
    change_paths: list[CriterionChangePath] = Field(min_length=1)
    paths_truncated: bool = False
    recheck_dates: list[CriterionRecheckDate] = Field(default_factory=list)
    explanation: str = Field(min_length=1)

    @model_validator(mode="after")
    def paths_and_dates_are_consistent(self) -> Self:
        if self.minimum_change_count != min(
            len(item.criterion_ids) for item in self.change_paths
        ):
            raise ValueError("minimum_change_count must match change_paths")
        if any(item.trial_id != self.trial_id for item in self.recheck_dates):
            raise ValueError("every recheck date must belong to trial_id")
        _require_unique(
            [item.criterion_id for item in self.recheck_dates],
            "recheck_dates.criterion_id",
        )
        return self
