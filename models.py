"""Pydantic v2 data models for ClarifyTrial Agent v1.2-final.

ClarifyTrial Agent is a shared-state multi-agent system for Interactive
Clinical Trial Recommendation. The models below encode the locked
architecture invariants:

- The Eligibility State Tracker is the central shared state.
- State is session-level and keyed by ``patient_id`` (``PatientSession``).
- Multiple trials are stored under ``trial_states_by_trial_id``.
- Each trial has its own ``trial_context`` and ``criterion_states``.
- Missing variables are deduplicated globally by ``missing_variable_key``
  (``global_missing_variable_pool``).
- Clarification questions live in a single global clarification queue,
  not per trial (``global_clarification_queue``).
- ``clarification_round_count`` is session-level with a max of 3.
- ``trial_relevance_score`` affects ranking only, never hard eligibility.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field

MAX_CLARIFICATION_ROUNDS = 3


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class CriterionType(str, Enum):
    inclusion = "inclusion"
    exclusion = "exclusion"


class CriterionMatchStatus(str, Enum):
    met = "met"
    unmet = "unmet"
    unknown = "unknown"
    conflict = "conflict"
    not_applicable = "not_applicable"


class EligibilityEffect(str, Enum):
    supports_eligibility = "supports_eligibility"
    blocks_eligibility = "blocks_eligibility"
    uncertain = "uncertain"
    neutral = "neutral"


class ReviewReason(str, Enum):
    conflicting_evidence = "conflicting_evidence"
    max_rounds_exceeded = "max_rounds_exceeded"
    safety_sensitive = "safety_sensitive"
    insufficient_evidence = "insufficient_evidence"


class Recommendation(str, Enum):
    likely_eligible = "likely_eligible"
    likely_ineligible = "likely_ineligible"
    uncertain = "uncertain"
    needs_human_review = "needs_human_review"


class QuestionStatus(str, Enum):
    pending = "pending"
    answered = "answered"


class MissingVariableStatus(str, Enum):
    pending = "pending"
    resolved = "resolved"


# ---------------------------------------------------------------------------
# Trial protocol & context
# ---------------------------------------------------------------------------


class TrialProtocol(BaseModel):
    """Source-agnostic trial protocol.

    Fields are chosen so a future ClinicalTrials.gov API v2 adapter can
    populate them, but no API-specific response paths are assumed.
    """

    trial_id: str
    nct_id: Optional[str] = None
    title: Optional[str] = None
    trial_description: Optional[str] = None
    inclusion_criteria_text: Optional[str] = None
    exclusion_criteria_text: Optional[str] = None
    eligibility_criteria_raw: str = ""
    conditions: list[str] = Field(default_factory=list)
    interventions: list[str] = Field(default_factory=list)
    source: str = "mock"
    source_url: Optional[str] = None
    retrieved_at: Optional[datetime] = None


class TrialContext(BaseModel):
    """Per-trial descriptive context.

    Supports relevance and explanation only. It must NOT create new
    blocking eligibility criteria unless the protocol explicitly states
    them as criteria.
    """

    trial_id: str
    description: str = ""
    conditions: list[str] = Field(default_factory=list)
    interventions: list[str] = Field(default_factory=list)
    relevance_notes: Optional[str] = None


class Criterion(BaseModel):
    """A single parsed eligibility criterion."""

    criterion_id: str
    trial_id: str
    criterion_type: CriterionType
    text: str
    required_variables: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Patient profile & evidence
# ---------------------------------------------------------------------------


class PatientProfile(BaseModel):
    """Normalized patient profile.

    Free-text clarification answers are normalized by the Patient Profile
    Understanding Agent into ``variables`` before any rule update.
    """

    patient_id: str
    age: Optional[int] = None
    sex: Optional[str] = None
    conditions: list[str] = Field(default_factory=list)
    medications: list[str] = Field(default_factory=list)
    variables: dict[str, Any] = Field(default_factory=dict)
    free_text_notes: Optional[str] = None


class SourceSentence(BaseModel):
    """A sentence from a source document used as evidence."""

    sentence_id: str
    text: str
    source: str = "patient_note"


class EvidenceContext(BaseModel):
    """Evidence gathered for evaluating one criterion."""

    criterion_id: str
    sentences: list[SourceSentence] = Field(default_factory=list)
    profile_fields_used: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Criterion state & missing variables
# ---------------------------------------------------------------------------


class CriterionState(BaseModel):
    """Match state of one criterion for the current patient."""

    criterion_id: str
    trial_id: str
    criterion_type: CriterionType
    criterion_match_status: CriterionMatchStatus = CriterionMatchStatus.unknown
    eligibility_effect: EligibilityEffect = EligibilityEffect.uncertain
    review_required: bool = False
    review_reason: Optional[ReviewReason] = None
    missing_variable_keys: list[str] = Field(default_factory=list)
    evidence: Optional[EvidenceContext] = None


class GlobalMissingVariablePoolItem(BaseModel):
    """A missing variable deduplicated globally by ``missing_variable_key``."""

    missing_variable_key: str
    status: MissingVariableStatus = MissingVariableStatus.pending
    affected_criterion_ids: list[str] = Field(default_factory=list)
    affected_trial_ids: list[str] = Field(default_factory=list)
    description: Optional[str] = None
    # Optional question priority ("high" | "medium" | "low"); assigned by
    # the Missing Information Detection layer, None when not yet assigned.
    priority: Optional[str] = None


class FollowUpQuestion(BaseModel):
    """A clarification question in the global clarification queue."""

    question_id: str
    missing_variable_key: str
    target_profile_field: str
    expected_answer_type: str
    allowed_values_or_schema: Optional[Any] = None
    affected_criterion_ids: list[str] = Field(default_factory=list)
    question_text: str
    priority_rank: int = 0
    status: QuestionStatus = QuestionStatus.pending


class AnswerUpdate(BaseModel):
    """A normalized answer applied to the shared state."""

    question_id: str
    missing_variable_key: str
    raw_answer_text: str
    normalized_value: Any = None
    updated_criterion_ids: list[str] = Field(default_factory=list)
    round_number: int = Field(default=1, ge=1, le=MAX_CLARIFICATION_ROUNDS)


# ---------------------------------------------------------------------------
# Recommendation & session state
# ---------------------------------------------------------------------------


class TrialRecommendation(BaseModel):
    """Final per-trial recommendation.

    ``trial_relevance_score`` influences ``ranking_score`` only; it never
    changes hard eligibility.
    """

    trial_id: str
    recommendation: Recommendation
    rank: Optional[int] = None
    trial_relevance_score: float = 0.0
    ranking_score: float = 0.0
    hard_filter_triggered: bool = False
    blocking_criteria: list[str] = Field(default_factory=list)
    supporting_criteria: list[str] = Field(default_factory=list)
    uncertain_criteria: list[str] = Field(default_factory=list)
    pending_questions: list[str] = Field(default_factory=list)


class TrialState(BaseModel):
    """Per-trial slice of the shared state."""

    trial_id: str
    trial_context: TrialContext
    criterion_states: list[CriterionState] = Field(default_factory=list)
    trial_relevance_score: float = 0.0


class PatientSession(BaseModel):
    """Session-level central shared state, keyed by ``patient_id``.

    This is the state owned by the Eligibility State Tracker Agent.
    """

    patient_id: str
    patient_profile: PatientProfile
    trial_states_by_trial_id: dict[str, TrialState] = Field(default_factory=dict)
    global_missing_variable_pool: dict[str, GlobalMissingVariablePoolItem] = Field(
        default_factory=dict
    )
    global_clarification_queue: list[FollowUpQuestion] = Field(default_factory=list)
    clarification_round_count: int = Field(
        default=0, ge=0, le=MAX_CLARIFICATION_ROUNDS
    )
    answer_updates: list[AnswerUpdate] = Field(default_factory=list)
    trial_recommendations: list[TrialRecommendation] = Field(default_factory=list)


class FinalOutput(BaseModel):
    """User-facing output of one session."""

    patient_id: str
    trial_recommendations: list[TrialRecommendation] = Field(default_factory=list)
    explanations: dict[str, str] = Field(default_factory=dict)
    pending_questions: list[FollowUpQuestion] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Synthetic validation datasets (harness only — not clinical truth)
# ---------------------------------------------------------------------------


class SyntheticPatientCase(BaseModel):
    """One professor-style synthetic patient case summary.

    These are natural-language patient INPUTS only — NOT eligibility ground
    truth. They validate the input contract of the Patient Profile
    Understanding Agent.
    """

    num: str
    title: str


class SyntheticPatientDataset(BaseModel):
    """Dataset of professor-style synthetic patient case summaries."""

    topics: list[SyntheticPatientCase] = Field(default_factory=list)


class SyntheticTrialProtocolDataset(BaseModel):
    """Dataset of mock, source-agnostic trial protocols."""

    trials: list[TrialProtocol] = Field(default_factory=list)


class SyntheticMatchingScenario(BaseModel):
    """A labeled synthetic matching scenario for rule validation.

    Separate from patient summaries: scenarios carry expected
    recommendation labels and expected missing variables, purely to test
    the locked rules — they are not clinical truth.
    """

    scenario_id: str
    patient_id: str
    trial_id: str
    patient_profile: PatientProfile
    expected_recommendation: Recommendation
    expected_missing_variables: list[str] = Field(default_factory=list)
    expected_blocking_criteria: list[str] = Field(default_factory=list)
    explanation_of_label: str


class SyntheticMatchingScenarioDataset(BaseModel):
    """Dataset of labeled synthetic matching scenarios."""

    scenarios: list[SyntheticMatchingScenario] = Field(default_factory=list)


class RequestLog(BaseModel):
    """Observability record for one agent action."""

    request_id: str
    patient_id: str
    trial_id: Optional[str] = None
    criterion_id: Optional[str] = None
    timestamp: datetime
    agent_name: str
    action: str
    criterion_match_status: Optional[CriterionMatchStatus] = None
    eligibility_effect: Optional[EligibilityEffect] = None
    latency_ms: Optional[float] = None
    model_version: Optional[str] = None
    error_code: Optional[str] = None
    input_ref: Optional[str] = None
    output_ref: Optional[str] = None
