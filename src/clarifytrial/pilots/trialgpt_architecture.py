"""Static, provider-neutral S1/M1/M2 benchmark on TrialGPT criteria.

The module deliberately keeps public/expert labels outside every model-visible
payload.  A frozen case (raw note, criteria, label policy, and one BM25
snapshot) is built once and reused by all three architecture arms.  Scoring is
performed only after every requested model call has finished.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict
from enum import StrEnum
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..datasets.trialgpt import (
    CriterionType,
    TrialGPTPair,
    TrialGPTPatientSplit,
    split_trialgpt_pairs_by_patient,
)
from ..disclaimer import DEFAULT_MEDICAL_DISCLAIMER
from ..llm.base import ModelCall, ModelUsage, StructuredModel
from ..retrieval.bm25 import BM25Retriever
from ..retrieval.models import SearchDocument
from ..retrieval.store import CriterionStore
from ..trace import TraceEvent, TraceRecorder


EligibilityLabel = Literal[
    "included",
    "not included",
    "excluded",
    "not excluded",
    "not enough information",
    "not applicable",
]

PINNED_COMPLETE_PAIR_COUNT = 104
PINNED_COMPLETE_CRITERION_COUNT = 1_011
PINNED_SPLIT_PAIR_COUNTS = {"development": 20, "heldout": 64, "overlap": 20}
PINNED_SPLIT_CRITERION_COUNTS = {
    "development": 211,
    "heldout": 654,
    "overlap": 146,
}
ORDER_SEED = 20_260_821
LEGACY_STATIC_ARCHITECTURE_PROTOCOL_ID = "trialgpt-static-architecture-v1"
STATIC_ARCHITECTURE_PROTOCOL_ID = "trialgpt-static-architecture-v2"
MISSINGNESS_POLICY_ID = "trialgpt-balanced-missingness-v1"
LABEL_SEMANTICS_ID = "trialgpt-public-label-semantics-v1"
STATIC_COORDINATOR_RULE_ID = "trialgpt-static-code-route-v2"
REVIEW_TRIGGER_ID = "trialgpt-static-all-matcher-nei-review-v2"
FINAL_STATUS_RULE_ID = "trialgpt-expert-criterion-aggregate-v1"
JUDGMENT_BATCHING_ID = "trialgpt-max-19-criteria-v1"
MAX_CRITERIA_PER_JUDGMENT_CALL = 19

SINGLE_ROLE = "trialgpt_architecture_single"
COORDINATOR_ROLE = "coordinator"
MATCHER_ROLE = "matcher_judge"
REVIEWER_ROLE = "selective_reviewer"
NEXT_EVIDENCE_ROLE = "next_evidence"

SINGLE_PROMPT_ID = "prompts/trialgpt_architecture_single.md"
MATCHER_PROMPT_ID = "prompts/trialgpt_architecture_matcher_judge_v2.md"
REVIEWER_PROMPT_ID = "prompts/trialgpt_architecture_reviewer_v2.md"

MEDICAL_DISCLAIMER = DEFAULT_MEDICAL_DISCLAIMER

MISSINGNESS_RULES = (
    "Use not applicable only when the criterion premise clearly does not apply.",
    "Use direct evidence or strong implicit evidence without inventing a missing event, test, date, or value.",
    "For an exclusion condition that a thorough note would normally document if present, documented absence may support not excluded when no contrary clue exists.",
    "For an inclusion requirement, absence never proves it occurred; use not included only for direct contradiction or the wrong required method/site, otherwise not enough information.",
    "Keep not enough information for missing exact values, scores, dates, windows, unperformed tests, future decisions, or unresolved compound requirements.",
)


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class _FrozenStrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, frozen=True)


class ArchitectureArm(StrEnum):
    S1 = "S1"
    M1 = "M1"
    M2 = "M2"


ARMS = (ArchitectureArm.S1, ArchitectureArm.M1, ArchitectureArm.M2)
ARM_ROTATIONS = (
    ARMS,
    (ArchitectureArm.M1, ArchitectureArm.M2, ArchitectureArm.S1),
    (ArchitectureArm.M2, ArchitectureArm.S1, ArchitectureArm.M1),
)


class TrialFinalStatus(StrEnum):
    ELIGIBLE = "eligible"
    INELIGIBLE = "ineligible"
    UNCERTAIN = "uncertain"


class EvidenceBasis(StrEnum):
    DIRECT = "direct_evidence"
    STRONG_IMPLICIT = "strong_implicit_evidence"
    EXPECTED_DOCUMENTATION_ABSENCE = "expected_documentation_absence"
    UNRESOLVED = "unresolved_information"
    CONFLICTING = "conflicting_evidence"
    NOT_APPLICABLE = "not_applicable"


class StaticReviewFlag(StrEnum):
    MATCHER_REQUESTED = "matcher_requested"
    EVIDENCE_CONFLICT = "evidence_conflict"
    EXPECTED_DOCUMENTATION_ABSENCE_CANDIDATE = (
        "expected_documentation_absence_candidate"
    )
    STRONG_IMPLICIT_EVIDENCE_CANDIDATE = "strong_implicit_evidence_candidate"
    UNSUPPORTED_DECISIVE_LABEL = "unsupported_decisive_label"


class RunStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"


class PatientSentenceHit(_FrozenStrictModel):
    rank: int = Field(ge=1)
    score: float
    sentence_id: int = Field(ge=0)
    sentence_text: str = Field(min_length=1)


class CriterionBM25Hits(_FrozenStrictModel):
    annotation_id: int = Field(ge=0)
    hits: tuple[PatientSentenceHit, ...]


class BM25Snapshot(_FrozenStrictModel):
    snapshot_id: str = Field(min_length=1)
    retriever: Literal["clarifytrial-bm25-v1"] = "clarifytrial-bm25-v1"
    top_k: int = Field(ge=1)
    criteria: tuple[CriterionBM25Hits, ...]

    @field_validator("criteria")
    @classmethod
    def criterion_ids_are_unique(
        cls, value: tuple[CriterionBM25Hits, ...]
    ) -> tuple[CriterionBM25Hits, ...]:
        identifiers = [item.annotation_id for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("BM25 snapshot repeats annotation_id")
        return value


class ArchitectureCriterionInput(_FrozenStrictModel):
    annotation_id: int = Field(ge=0)
    criterion_type: CriterionType
    criterion_text: str = Field(min_length=1)
    allowed_labels: tuple[EligibilityLabel, ...]


class ArchitectureTrialContext(_FrozenStrictModel):
    title: str = Field(min_length=1)
    target_diseases: tuple[str, ...] = ()
    interventions: tuple[str, ...] = ()
    summary: str = ""


class TrialGPTArchitectureCase(_FrozenStrictModel):
    case_id: str = Field(min_length=1)
    pair_id: str = Field(min_length=1)
    patient_id: str = Field(min_length=1)
    trial_id: str = Field(min_length=1)
    raw_patient_note: str = Field(min_length=1)
    trial: ArchitectureTrialContext
    criteria: tuple[ArchitectureCriterionInput, ...]
    bm25_snapshot: BM25Snapshot
    missingness_policy_id: Literal["trialgpt-balanced-missingness-v1"] = (
        MISSINGNESS_POLICY_ID
    )
    missingness_rules: tuple[str, ...] = MISSINGNESS_RULES
    label_semantics_id: Literal["trialgpt-public-label-semantics-v1"] = (
        LABEL_SEMANTICS_ID
    )
    final_status_rule_id: Literal["trialgpt-expert-criterion-aggregate-v1"] = (
        FINAL_STATUS_RULE_ID
    )

    @model_validator(mode="after")
    def criteria_match_snapshot(self) -> "TrialGPTArchitectureCase":
        criterion_ids = [item.annotation_id for item in self.criteria]
        snapshot_ids = [item.annotation_id for item in self.bm25_snapshot.criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("case criteria repeat annotation_id")
        if criterion_ids != snapshot_ids:
            raise ValueError("case criteria and BM25 snapshot must have identical order")
        return self


class ArchitectureCriterionJudgment(_StrictModel):
    annotation_id: int = Field(ge=0)
    explanation: str = Field(min_length=1)
    evidence_sentence_ids: tuple[int, ...] = ()
    eligibility_label: EligibilityLabel
    evidence_basis: EvidenceBasis
    review_flags: tuple[StaticReviewFlag, ...] = ()

    @field_validator("evidence_sentence_ids")
    @classmethod
    def sentence_ids_are_unique_non_negative(
        cls, value: tuple[int, ...]
    ) -> tuple[int, ...]:
        if any(item < 0 for item in value) or len(value) != len(set(value)):
            raise ValueError("evidence sentence IDs must be unique and non-negative")
        return value

    @field_validator("review_flags")
    @classmethod
    def review_flags_are_unique(
        cls, value: tuple[StaticReviewFlag, ...]
    ) -> tuple[StaticReviewFlag, ...]:
        if len(value) != len(set(value)):
            raise ValueError("review flags must be unique")
        return value


class ArchitectureSingleResponse(_StrictModel):
    judgments: tuple[ArchitectureCriterionJudgment, ...] = Field(min_length=1)
    final_status: TrialFinalStatus


class ArchitectureMatcherResponse(_StrictModel):
    judgments: tuple[ArchitectureCriterionJudgment, ...] = Field(min_length=1)


class ArchitectureReviewerResponse(_StrictModel):
    reviews: tuple[ArchitectureCriterionJudgment, ...] = Field(min_length=1)


class ArchitectureCallRecord(_StrictModel):
    call_id: str
    role: str
    prompt_id: str
    status: Literal["completed", "failed"]
    usage: dict[str, Any] | None = None
    error_type: str | None = None
    error: str | None = None


class ArchitectureScore(_StrictModel):
    criteria_total: int = Field(ge=0)
    expert_label_correct: int = Field(ge=0)
    public_label_agreement: int = Field(ge=0)
    evidence_exact: int = Field(ge=0)
    evidence_true_positive: int = Field(ge=0)
    evidence_false_positive: int = Field(ge=0)
    evidence_false_negative: int = Field(ge=0)
    expert_nei_total: int = Field(ge=0)
    expert_nei_preserved: int = Field(ge=0)
    expert_decisive_total: int = Field(ge=0)
    decisive_to_nei: int = Field(ge=0)
    expert_final_status: TrialFinalStatus
    public_final_status: TrialFinalStatus
    expert_final_correct: bool
    pre_review_expert_label_correct: int | None = Field(default=None, ge=0)
    review_wrong_to_correct: int = Field(default=0, ge=0)
    review_correct_to_wrong: int = Field(default=0, ge=0)


class ArchitectureArmResult(_StrictModel):
    protocol_id: str = LEGACY_STATIC_ARCHITECTURE_PROTOCOL_ID
    run_id: str
    case_id: str
    pair_id: str
    arm: ArchitectureArm
    planned_arm_order: tuple[ArchitectureArm, ArchitectureArm, ArchitectureArm]
    status: RunStatus
    predictions: tuple[ArchitectureCriterionJudgment, ...] = ()
    pre_review_predictions: tuple[ArchitectureCriterionJudgment, ...] = ()
    reported_final_status: TrialFinalStatus | None = None
    final_status: TrialFinalStatus | None = None
    review_trigger_id: str | None = None
    review_selected_ids: tuple[int, ...] = ()
    review_changed_count: int = Field(default=0, ge=0)
    static_coordinator_rule_id: str | None = None
    judgment_batching_id: str = JUDGMENT_BATCHING_ID
    judgment_batch_count: int = Field(default=1, ge=1)
    role_call_counts: dict[str, int]
    next_evidence_model_calls: Literal[0] = 0
    calls: tuple[ArchitectureCallRecord, ...]
    trace: tuple[TraceEvent, ...]
    failure_stage: str | None = None
    failure_type: str | None = None
    failure: str | None = None
    score: ArchitectureScore | None = None


class ArchitectureCasePlan(_StrictModel):
    case_id: str
    pair_id: str
    execution_rank: int = Field(ge=0)
    arm_order: tuple[ArchitectureArm, ArchitectureArm, ArchitectureArm]


class ArmAggregateMetrics(_StrictModel):
    arm: ArchitectureArm
    cases: int = Field(ge=0)
    criteria: int = Field(ge=0)
    criterion_accuracy: float = Field(ge=0, le=1)
    evidence_exact_rate: float = Field(ge=0, le=1)
    evidence_micro_f1: float = Field(ge=0, le=1)
    trial_status_accuracy: float = Field(ge=0, le=1)
    expert_nei_preservation: float = Field(ge=0, le=1)
    decisive_to_nei_rate: float = Field(ge=0, le=1)
    review_selected: int = Field(ge=0)
    review_changed: int = Field(ge=0)
    pre_review_criterion_accuracy: float | None = Field(
        default=None, ge=0, le=1
    )
    review_accuracy_delta: float | None = Field(default=None, ge=-1, le=1)
    review_wrong_to_correct: int = Field(default=0, ge=0)
    review_correct_to_wrong: int = Field(default=0, ge=0)


class PairedArmMetrics(_StrictModel):
    arm_a: ArchitectureArm
    arm_b: ArchitectureArm
    paired_cases: int = Field(ge=0)
    criteria_compared: int = Field(ge=0)
    wrong_to_correct: int = Field(ge=0)
    correct_to_wrong: int = Field(ge=0)
    trial_wrong_to_correct: int = Field(ge=0)
    trial_correct_to_wrong: int = Field(ge=0)


class TrialGPTArchitectureBenchmark(_StrictModel):
    protocol_id: str = LEGACY_STATIC_ARCHITECTURE_PROTOCOL_ID
    run_kind: Literal["trialgpt_static_architecture_benchmark"] = (
        "trialgpt_static_architecture_benchmark"
    )
    static_coordinator_rule_id: str | None = None
    order_seed: int
    plans: tuple[ArchitectureCasePlan, ...]
    results: tuple[ArchitectureArmResult, ...]
    arm_role_call_counts: dict[str, dict[str, int]]
    next_evidence_model_calls: Literal[0] = 0
    arm_metrics: dict[str, ArmAggregateMetrics]
    paired_metrics: tuple[PairedArmMetrics, ...]
    disclaimer: str = MEDICAL_DISCLAIMER


class TrialGPTArchitectureSplit(_StrictModel):
    development: tuple[TrialGPTArchitectureCase, ...]
    heldout: tuple[TrialGPTArchitectureCase, ...]
    overlap: tuple[TrialGPTArchitectureCase, ...]


CaseCompletedCallback = Callable[
    [ArchitectureCasePlan, tuple[ArchitectureArmResult, ...]], None
]


def _allowed_labels(criterion_type: CriterionType) -> tuple[EligibilityLabel, ...]:
    if criterion_type == "inclusion":
        return ("included", "not included", "not enough information", "not applicable")
    return ("excluded", "not excluded", "not enough information", "not applicable")


def pair_id(pair: TrialGPTPair) -> str:
    """Return the existing public patient/trial identifier used by smoke selection."""

    return f"{pair.patient_id}/{pair.trial_id}"


def select_architecture_pairs(
    pairs: Sequence[TrialGPTPair], pair_ids: Sequence[str]
) -> list[TrialGPTPair]:
    """Select explicit pairs in requested order and reject missing/duplicate IDs."""

    if len(pair_ids) != len(set(pair_ids)):
        raise ValueError("explicit pair IDs must be unique")
    by_id = {pair_id(item): item for item in pairs}
    if len(by_id) != len(pairs):
        raise ValueError("source patient-trial pair IDs must be unique")
    missing = [identifier for identifier in pair_ids if identifier not in by_id]
    if missing:
        raise ValueError("unknown patient-trial pair IDs: " + ", ".join(missing))
    return [by_id[identifier] for identifier in pair_ids]


def _numbered_sentences(note: str) -> tuple[tuple[int, str], ...]:
    sentences: list[tuple[int, str]] = []
    for line in note.splitlines():
        match = re.match(r"^\s*(\d+)\.\s*(.+?)\s*$", line)
        if match:
            sentences.append((int(match.group(1)), match.group(2)))
    identifiers = [identifier for identifier, _ in sentences]
    if not sentences:
        raise ValueError("patient note has no numbered sentences")
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("patient note repeats a sentence ID")
    return tuple(sentences)


def _digest(value: Any, *, size: int = 24) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:size]


def build_architecture_case(
    pair: TrialGPTPair, *, retrieval_top_k: int = 5
) -> TrialGPTArchitectureCase:
    """Build one gold-free frozen input and its criterion-to-sentence BM25 snapshot."""

    if retrieval_top_k < 1:
        raise ValueError("retrieval_top_k must be at least one")
    if any(row.criterion_text is None for row in pair.criteria):
        raise ValueError("architecture benchmark requires complete criterion text")
    sentences = _numbered_sentences(pair.note)
    store = CriterionStore(
        SearchDocument(
            document_id=f"sentence-{sentence_id:06d}",
            trial_id=pair.trial_id,
            criterion_id="patient-note",
            criterion_type="patient_note",
            raw_text=text,
            source_location=f"{pair.patient_id}#sentence-{sentence_id}",
            metadata={"sentence_id": sentence_id},
        )
        for sentence_id, text in sentences
    )
    retriever = BM25Retriever(store)
    ordered_rows = sorted(pair.criteria, key=lambda row: row.annotation_id)
    criterion_inputs: list[ArchitectureCriterionInput] = []
    retrieval_rows: list[CriterionBM25Hits] = []
    for row in ordered_rows:
        criterion_text = row.criterion_text
        assert criterion_text is not None
        criterion_inputs.append(
            ArchitectureCriterionInput(
                annotation_id=row.annotation_id,
                criterion_type=row.criterion_type,
                criterion_text=criterion_text,
                allowed_labels=_allowed_labels(row.criterion_type),
            )
        )
        hits = retriever.search(
            criterion_text, top_k=min(retrieval_top_k, len(sentences))
        )
        retrieval_rows.append(
            CriterionBM25Hits(
                annotation_id=row.annotation_id,
                hits=tuple(
                    PatientSentenceHit(
                        rank=hit.rank,
                        score=hit.score,
                        sentence_id=int(hit.document.metadata["sentence_id"]),
                        sentence_text=hit.document.raw_text,
                    )
                    for hit in hits
                ),
            )
        )
    snapshot_body = [item.model_dump(mode="json") for item in retrieval_rows]
    snapshot = BM25Snapshot(
        snapshot_id=f"bm25-{_digest(snapshot_body)}",
        top_k=retrieval_top_k,
        criteria=tuple(retrieval_rows),
    )
    metadata = pair.metadata
    trial = ArchitectureTrialContext(
        title=pair.trial_title if metadata is None else metadata.brief_title,
        target_diseases=() if metadata is None else tuple(metadata.diseases_list),
        interventions=() if metadata is None else tuple(metadata.drugs_list),
        summary="" if metadata is None else metadata.brief_summary,
    )
    public_body = {
        "pair_id": pair_id(pair),
        "note": pair.note,
        "trial": trial.model_dump(mode="json"),
        "criteria": [item.model_dump(mode="json") for item in criterion_inputs],
        "snapshot_id": snapshot.snapshot_id,
        "missingness_policy_id": MISSINGNESS_POLICY_ID,
    }
    return TrialGPTArchitectureCase(
        case_id=f"trialgpt-case-{_digest(public_body)}",
        pair_id=pair_id(pair),
        patient_id=pair.patient_id,
        trial_id=pair.trial_id,
        raw_patient_note=pair.note,
        trial=trial,
        criteria=tuple(criterion_inputs),
        bm25_snapshot=snapshot,
    )


def _judgment_batches(
    case: TrialGPTArchitectureCase,
) -> tuple[TrialGPTArchitectureCase, ...]:
    """Split only large judgment payloads while preserving criterion order."""

    if len(case.criteria) <= MAX_CRITERIA_PER_JUDGMENT_CALL:
        return (case,)
    count = (
        len(case.criteria) + MAX_CRITERIA_PER_JUDGMENT_CALL - 1
    ) // MAX_CRITERIA_PER_JUDGMENT_CALL
    batches: list[TrialGPTArchitectureCase] = []
    for index in range(count):
        start = index * MAX_CRITERIA_PER_JUDGMENT_CALL
        stop = start + MAX_CRITERIA_PER_JUDGMENT_CALL
        criteria = case.criteria[start:stop]
        retrieval_rows = case.bm25_snapshot.criteria[start:stop]
        snapshot = case.bm25_snapshot.model_copy(
            update={
                "snapshot_id": (
                    f"{case.bm25_snapshot.snapshot_id}-batch-{index + 1}-of-{count}"
                ),
                "criteria": retrieval_rows,
            }
        )
        batches.append(
            case.model_copy(
                update={"criteria": criteria, "bm25_snapshot": snapshot}
            )
        )
    return tuple(batches)


def _batched_call_id(base: str, *, index: int, count: int) -> str:
    if count == 1:
        return base
    return f"{base}:batch-{index + 1}-of-{count}"


def _chunks(values: Sequence[int], *, size: int) -> tuple[tuple[int, ...], ...]:
    return tuple(
        tuple(values[start : start + size])
        for start in range(0, len(values), size)
    )


def build_architecture_patient_split(
    pairs: Sequence[TrialGPTPair],
    *,
    retrieval_top_k: int = 5,
    require_pinned_counts: bool = True,
) -> TrialGPTArchitectureSplit:
    """Reuse the established patient split and optionally enforce 104/1,011 counts."""

    split: TrialGPTPatientSplit = split_trialgpt_pairs_by_patient(pairs)
    partitions = {
        "development": split.development_pairs,
        "heldout": split.held_out_pairs,
        "overlap": split.overlap_patient_pairs,
    }
    if require_pinned_counts:
        pair_counts = {name: len(items) for name, items in partitions.items()}
        criterion_counts = {
            name: sum(len(pair.criteria) for pair in items)
            for name, items in partitions.items()
        }
        if pair_counts != PINNED_SPLIT_PAIR_COUNTS:
            raise ValueError(f"unexpected complete-pair split counts: {pair_counts}")
        if criterion_counts != PINNED_SPLIT_CRITERION_COUNTS:
            raise ValueError(f"unexpected complete-criterion split counts: {criterion_counts}")
        if sum(pair_counts.values()) != PINNED_COMPLETE_PAIR_COUNT:
            raise AssertionError("pinned complete-pair total changed")
        if sum(criterion_counts.values()) != PINNED_COMPLETE_CRITERION_COUNT:
            raise AssertionError("pinned complete-criterion total changed")
    return TrialGPTArchitectureSplit(
        development=tuple(
            build_architecture_case(item, retrieval_top_k=retrieval_top_k)
            for item in split.development_pairs
        ),
        heldout=tuple(
            build_architecture_case(item, retrieval_top_k=retrieval_top_k)
            for item in split.held_out_pairs
        ),
        overlap=tuple(
            build_architecture_case(item, retrieval_top_k=retrieval_top_k)
            for item in split.overlap_patient_pairs
        ),
    )


def plan_architecture_arm_orders(
    cases: Sequence[TrialGPTArchitectureCase], *, seed: int = ORDER_SEED
) -> tuple[ArchitectureCasePlan, ...]:
    """Assign the three cyclic arm orders with counts differing by at most one."""

    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("architecture cases must have unique stable case IDs")
    ranked = sorted(
        cases,
        key=lambda case: (
            _digest({"seed": seed, "case_id": case.case_id}, size=64),
            case.case_id,
        ),
    )
    return tuple(
        ArchitectureCasePlan(
            case_id=case.case_id,
            pair_id=case.pair_id,
            execution_rank=index,
            arm_order=ARM_ROTATIONS[index % len(ARM_ROTATIONS)],
        )
        for index, case in enumerate(ranked)
    )


def aggregate_trial_status(
    labels: Sequence[tuple[CriterionType, EligibilityLabel]],
) -> TrialFinalStatus:
    """Explicit TrialGPT rule: violation > unresolved > eligible."""

    if not labels:
        raise ValueError("at least one criterion label is required")
    for criterion_type, label in labels:
        if label not in _allowed_labels(criterion_type):
            raise ValueError(f"label {label!r} is invalid for {criterion_type}")
    violation = any(
        (kind == "inclusion" and label == "not included")
        or (kind == "exclusion" and label == "excluded")
        for kind, label in labels
    )
    if violation:
        return TrialFinalStatus.INELIGIBLE
    if any(label == "not enough information" for _, label in labels):
        return TrialFinalStatus.UNCERTAIN
    return TrialFinalStatus.ELIGIBLE


def select_m2_review_targets(
    judgments: Sequence[ArchitectureCriterionJudgment],
) -> dict[int, tuple[str, ...]]:
    """Review every matcher NEI and never revisit a decisive matcher label."""

    return {
        item.annotation_id: ("initial_matcher_nei",)
        for item in judgments
        if item.eligibility_label == "not enough information"
    }


def _validate_judgments(
    judgments: Sequence[ArchitectureCriterionJudgment],
    case: TrialGPTArchitectureCase,
    *,
    expected_ids: Sequence[int] | None = None,
) -> None:
    expected = (
        {item.annotation_id for item in case.criteria}
        if expected_ids is None
        else set(expected_ids)
    )
    actual = {item.annotation_id for item in judgments}
    if len(actual) != len(judgments) or actual != expected:
        raise ValueError("model output must cover exactly the requested annotation IDs")
    criteria = {item.annotation_id: item for item in case.criteria}
    sentence_ids = {identifier for identifier, _ in _numbered_sentences(case.raw_patient_note)}
    for judgment in judgments:
        criterion = criteria[judgment.annotation_id]
        if judgment.eligibility_label not in criterion.allowed_labels:
            raise ValueError("model output used a label from the wrong criterion type")
        if not set(judgment.evidence_sentence_ids) <= sentence_ids:
            raise ValueError("model output cited a sentence ID absent from the note")


def validate_m2_review_transition(
    initial: ArchitectureCriterionJudgment,
    reviewed: ArchitectureCriterionJudgment,
    criterion: ArchitectureCriterionInput,
) -> None:
    """Enforce the narrow, auditable transition allowed to the static reviewer."""

    if initial.annotation_id != reviewed.annotation_id:
        raise ValueError("review output changed the annotation ID")
    if initial.annotation_id != criterion.annotation_id:
        raise ValueError("review transition references the wrong criterion")
    if initial.eligibility_label != "not enough information":
        raise ValueError("M2 may review only an initial matcher NEI")
    if reviewed.eligibility_label not in criterion.allowed_labels:
        raise ValueError("review output used a label from the wrong criterion type")
    if reviewed.eligibility_label == "not applicable":
        raise ValueError("M2 reviewer may not replace NEI with not applicable")
    if reviewed.eligibility_label == "not enough information":
        return
    if reviewed.evidence_basis is EvidenceBasis.EXPECTED_DOCUMENTATION_ABSENCE:
        if not (
            criterion.criterion_type == "exclusion"
            and reviewed.eligibility_label == "not excluded"
        ):
            raise ValueError(
                "expected documentation absence only supports not excluded "
                "for an exclusion criterion"
            )
        return
    if reviewed.evidence_basis not in {
        EvidenceBasis.DIRECT,
        EvidenceBasis.STRONG_IMPLICIT,
    }:
        raise ValueError(
            "a decisive M2 review requires direct or strong implicit evidence"
        )


def _prediction_status(
    predictions: Sequence[ArchitectureCriterionJudgment],
    case: TrialGPTArchitectureCase,
) -> TrialFinalStatus:
    by_id = {item.annotation_id: item for item in predictions}
    return aggregate_trial_status(
        [
            (criterion.criterion_type, by_id[criterion.annotation_id].eligibility_label)
            for criterion in case.criteria
        ]
    )


def _invoke(
    model: StructuredModel,
    *,
    recorder: TraceRecorder,
    call_id: str,
    role: str,
    prompt_id: str,
    payload: Mapping[str, Any],
    response_model: type[BaseModel],
    records: list[ArchitectureCallRecord],
    counts: Counter[str],
) -> BaseModel | None:
    counts[role] += 1
    try:
        raw, usage = model.complete(
            ModelCall(
                role=role,
                prompt_id=prompt_id,
                payload=payload,
                response_model=response_model,
            )
        )
        if isinstance(raw, response_model):
            response = raw
        elif isinstance(raw, BaseModel):
            response = response_model.model_validate(raw.model_dump(mode="json"))
        else:
            response = response_model.model_validate(raw)
    except Exception as exc:
        error = str(exc)[:2_000]
        records.append(
            ArchitectureCallRecord(
                call_id=call_id,
                role=role,
                prompt_id=prompt_id,
                status="failed",
                error_type=type(exc).__name__,
                error=error,
            )
        )
        recorder.record(
            cycle=0,
            actor=role,
            event="structured_model_failed",
            input_refs=[call_id],
            output={"prompt_id": prompt_id, "error_type": type(exc).__name__, "error": error},
        )
        return None
    records.append(
        ArchitectureCallRecord(
            call_id=call_id,
            role=role,
            prompt_id=prompt_id,
            status="completed",
            usage=asdict(usage),
        )
    )
    recorder.record(
        cycle=0,
        actor=role,
        event="structured_model_completed",
        input_refs=[call_id],
        output={
            "prompt_id": prompt_id,
            "response_model": response_model.__name__,
            "response": response.model_dump(mode="json"),
        },
        usage=usage,
    )
    return response


def _failed_result(
    *,
    case: TrialGPTArchitectureCase,
    arm: ArchitectureArm,
    order: tuple[ArchitectureArm, ArchitectureArm, ArchitectureArm],
    recorder: TraceRecorder,
    records: list[ArchitectureCallRecord],
    counts: Counter[str],
    stage: str,
    exc: Exception | None = None,
    partial_predictions: Sequence[ArchitectureCriterionJudgment] = (),
    pre_review_predictions: Sequence[ArchitectureCriterionJudgment] = (),
    review_selected_ids: Sequence[int] = (),
    judgment_batch_count: int | None = None,
) -> ArchitectureArmResult:
    failure_record = next((item for item in reversed(records) if item.status == "failed"), None)
    complete_prediction_ids = {item.annotation_id for item in case.criteria}
    partial_prediction_ids = {item.annotation_id for item in partial_predictions}
    return ArchitectureArmResult(
        protocol_id=STATIC_ARCHITECTURE_PROTOCOL_ID,
        run_id=f"{case.case_id}:{arm.value}",
        case_id=case.case_id,
        pair_id=case.pair_id,
        arm=arm,
        planned_arm_order=order,
        status=RunStatus.PARTIAL if partial_predictions else RunStatus.FAILED,
        predictions=tuple(partial_predictions),
        pre_review_predictions=tuple(pre_review_predictions),
        final_status=(
            _prediction_status(partial_predictions, case)
            if partial_prediction_ids == complete_prediction_ids
            else None
        ),
        review_trigger_id=REVIEW_TRIGGER_ID if arm is ArchitectureArm.M2 else None,
        review_selected_ids=tuple(review_selected_ids),
        static_coordinator_rule_id=(
            STATIC_COORDINATOR_RULE_ID
            if arm in {ArchitectureArm.M1, ArchitectureArm.M2}
            else None
        ),
        judgment_batch_count=(
            len(_judgment_batches(case))
            if judgment_batch_count is None
            else judgment_batch_count
        ),
        role_call_counts={role: counts[role] for role in _all_roles()},
        calls=tuple(records),
        trace=tuple(recorder.events),
        failure_stage=stage,
        failure_type=(
            type(exc).__name__ if exc is not None else (
                None if failure_record is None else failure_record.error_type
            )
        ),
        failure=(
            str(exc)[:2_000] if exc is not None else (
                None if failure_record is None else failure_record.error
            )
        ),
    )


def _all_roles() -> tuple[str, ...]:
    return (SINGLE_ROLE, COORDINATOR_ROLE, MATCHER_ROLE, REVIEWER_ROLE, NEXT_EVIDENCE_ROLE)


def _run_arm(
    case: TrialGPTArchitectureCase,
    arm: ArchitectureArm,
    order: tuple[ArchitectureArm, ArchitectureArm, ArchitectureArm],
    model: StructuredModel,
) -> ArchitectureArmResult:
    run_id = f"{case.case_id}:{arm.value}"
    judgment_batches = _judgment_batches(case)
    judgment_batch_count = len(judgment_batches)
    recorder = TraceRecorder(run_id)
    records: list[ArchitectureCallRecord] = []
    counts: Counter[str] = Counter()
    recorder.record(
        cycle=0,
        actor="benchmark_runner",
        event="static_dataset_constraints",
        input_refs=[case.case_id, case.bm25_snapshot.snapshot_id],
        output={
            "protocol_id": STATIC_ARCHITECTURE_PROTOCOL_ID,
            "arm": arm.value,
            "planned_arm_order": [item.value for item in order],
            "next_evidence_model_calls": 0,
            "missingness_policy_id": case.missingness_policy_id,
            "review_trigger_id": REVIEW_TRIGGER_ID if arm is ArchitectureArm.M2 else None,
            "static_coordinator_rule_id": (
                STATIC_COORDINATOR_RULE_ID
                if arm in {ArchitectureArm.M1, ArchitectureArm.M2}
                else None
            ),
            "judgment_batching_id": JUDGMENT_BATCHING_ID,
            "max_criteria_per_judgment_call": MAX_CRITERIA_PER_JUDGMENT_CALL,
            "judgment_batch_count": judgment_batch_count,
        },
    )
    if arm is ArchitectureArm.S1:
        accumulated: list[ArchitectureCriterionJudgment] = []
        reported_statuses: list[TrialFinalStatus] = []
        for batch_index, batch in enumerate(judgment_batches):
            response = _invoke(
                model,
                recorder=recorder,
                call_id=_batched_call_id(
                    f"{run_id}:single",
                    index=batch_index,
                    count=judgment_batch_count,
                ),
                role=SINGLE_ROLE,
                prompt_id=SINGLE_PROMPT_ID,
                payload={"shared_input": batch.model_dump(mode="json")},
                response_model=ArchitectureSingleResponse,
                records=records,
                counts=counts,
            )
            if response is None:
                return _failed_result(
                    case=case,
                    arm=arm,
                    order=order,
                    recorder=recorder,
                    records=records,
                    counts=counts,
                    stage="single",
                    partial_predictions=accumulated,
                    judgment_batch_count=judgment_batch_count,
                )
            typed = ArchitectureSingleResponse.model_validate(response)
            try:
                _validate_judgments(typed.judgments, batch)
                aggregated = _prediction_status(typed.judgments, batch)
                if typed.final_status is not aggregated:
                    raise ValueError(
                        "S1 reported final status disagrees with the common code rule"
                    )
            except Exception as exc:
                recorder.record(
                    cycle=0,
                    actor="benchmark_runner",
                    event="protocol_validation_failed",
                    output={"error": str(exc)},
                )
                return _failed_result(
                    case=case,
                    arm=arm,
                    order=order,
                    recorder=recorder,
                    records=records,
                    counts=counts,
                    stage="single_validation",
                    exc=exc,
                    partial_predictions=accumulated,
                    judgment_batch_count=judgment_batch_count,
                )
            by_id = {item.annotation_id: item for item in typed.judgments}
            accumulated.extend(by_id[item.annotation_id] for item in batch.criteria)
            reported_statuses.append(typed.final_status)
        predictions = tuple(accumulated)
        reported = reported_statuses[0] if judgment_batch_count == 1 else None
        selected: tuple[int, ...] = ()
        changed = 0
        pre_review_predictions: tuple[ArchitectureCriterionJudgment, ...] = ()
    else:
        target_refs = [f"annotation:{item.annotation_id}" for item in case.criteria]
        recorder.record(
            cycle=0,
            actor=COORDINATOR_ROLE,
            event="deterministic_route_selected",
            input_refs=target_refs,
            output={
                "rule_id": STATIC_COORDINATOR_RULE_ID,
                "route": "MATCHER_JUDGE",
                "target_ids": target_refs,
                "next_evidence_available": False,
                "model_calls": 0,
            },
        )
        accumulated = []
        for batch_index, batch in enumerate(judgment_batches):
            matcher = _invoke(
                model,
                recorder=recorder,
                call_id=_batched_call_id(
                    f"{run_id}:matcher_judge",
                    index=batch_index,
                    count=judgment_batch_count,
                ),
                role=MATCHER_ROLE,
                prompt_id=MATCHER_PROMPT_ID,
                payload={"shared_input": batch.model_dump(mode="json")},
                response_model=ArchitectureMatcherResponse,
                records=records,
                counts=counts,
            )
            if matcher is None:
                return _failed_result(
                    case=case,
                    arm=arm,
                    order=order,
                    recorder=recorder,
                    records=records,
                    counts=counts,
                    stage="matcher_judge",
                    partial_predictions=accumulated,
                    judgment_batch_count=judgment_batch_count,
                )
            matched = ArchitectureMatcherResponse.model_validate(matcher)
            try:
                _validate_judgments(matched.judgments, batch)
            except Exception as exc:
                return _failed_result(
                    case=case,
                    arm=arm,
                    order=order,
                    recorder=recorder,
                    records=records,
                    counts=counts,
                    stage="matcher_validation",
                    exc=exc,
                    partial_predictions=accumulated,
                    judgment_batch_count=judgment_batch_count,
                )
            by_id = {item.annotation_id: item for item in matched.judgments}
            accumulated.extend(by_id[item.annotation_id] for item in batch.criteria)
        predictions = tuple(accumulated)
        reported = None
        selected = ()
        changed = 0
        pre_review_predictions = (
            predictions if arm is ArchitectureArm.M2 else ()
        )
        if arm is ArchitectureArm.M2:
            triggers = select_m2_review_targets(predictions)
            selected = tuple(sorted(triggers))
            review_batches = _chunks(
                selected, size=MAX_CRITERIA_PER_JUDGMENT_CALL
            )
            recorder.record(
                cycle=0,
                actor="review_trigger",
                event="selective_review_targets_selected",
                input_refs=[f"annotation:{item}" for item in selected],
                output={
                    "trigger_id": REVIEW_TRIGGER_ID,
                    "reasons": triggers,
                    "selected_count": len(selected),
                    "review_batch_count": len(review_batches),
                    "max_criteria_per_review_call": MAX_CRITERIA_PER_JUDGMENT_CALL,
                },
            )
            if selected:
                initial = {item.annotation_id: item for item in predictions}
                criteria_by_id = {item.annotation_id: item for item in case.criteria}
                retrieval_by_id = {item.annotation_id: item for item in case.bm25_snapshot.criteria}
                review_batch_count = len(review_batches)
                for batch_index, review_ids in enumerate(review_batches):
                    reviewer = _invoke(
                        model,
                        recorder=recorder,
                        call_id=_batched_call_id(
                            f"{run_id}:selective_reviewer",
                            index=batch_index,
                            count=review_batch_count,
                        ),
                        role=REVIEWER_ROLE,
                        prompt_id=REVIEWER_PROMPT_ID,
                        payload={
                            "case_id": case.case_id,
                            "raw_patient_note": case.raw_patient_note,
                            "trial": case.trial.model_dump(mode="json"),
                            "criteria": [
                                criteria_by_id[item].model_dump(mode="json")
                                for item in review_ids
                            ],
                            "bm25_snapshot": [
                                retrieval_by_id[item].model_dump(mode="json")
                                for item in review_ids
                            ],
                            "missingness_policy_id": case.missingness_policy_id,
                            "missingness_rules": list(case.missingness_rules),
                            "label_semantics_id": case.label_semantics_id,
                            "initial_judgments": [
                                initial[item].model_dump(mode="json")
                                for item in review_ids
                            ],
                            "review_reasons": {
                                str(key): list(triggers[key]) for key in review_ids
                            },
                        },
                        response_model=ArchitectureReviewerResponse,
                        records=records,
                        counts=counts,
                    )
                    predictions = tuple(
                        initial[item.annotation_id] for item in case.criteria
                    )
                    if reviewer is None:
                        return _failed_result(
                            case=case,
                            arm=arm,
                            order=order,
                            recorder=recorder,
                            records=records,
                            counts=counts,
                            stage="selective_reviewer",
                            partial_predictions=predictions,
                            pre_review_predictions=pre_review_predictions,
                            review_selected_ids=selected,
                            judgment_batch_count=judgment_batch_count,
                        )
                    reviewed = ArchitectureReviewerResponse.model_validate(reviewer)
                    try:
                        _validate_judgments(
                            reviewed.reviews, case, expected_ids=review_ids
                        )
                        for item in reviewed.reviews:
                            validate_m2_review_transition(
                                initial[item.annotation_id],
                                item,
                                criteria_by_id[item.annotation_id],
                            )
                    except Exception as exc:
                        return _failed_result(
                            case=case,
                            arm=arm,
                            order=order,
                            recorder=recorder,
                            records=records,
                            counts=counts,
                            stage="review_validation",
                            exc=exc,
                            partial_predictions=predictions,
                            pre_review_predictions=pre_review_predictions,
                            review_selected_ids=selected,
                            judgment_batch_count=judgment_batch_count,
                        )
                    for item in reviewed.reviews:
                        if (
                            item.eligibility_label
                            != initial[item.annotation_id].eligibility_label
                        ):
                            changed += 1
                        initial[item.annotation_id] = item
                predictions = tuple(
                    initial[item.annotation_id] for item in case.criteria
                )
    final_status = _prediction_status(predictions, case)
    recorder.record(
        cycle=0,
        actor="decision_rules",
        event="trial_status_aggregated",
        input_refs=[f"annotation:{item.annotation_id}" for item in case.criteria],
        output={"rule_id": FINAL_STATUS_RULE_ID, "final_status": final_status.value},
    )
    return ArchitectureArmResult(
        protocol_id=STATIC_ARCHITECTURE_PROTOCOL_ID,
        run_id=run_id,
        case_id=case.case_id,
        pair_id=case.pair_id,
        arm=arm,
        planned_arm_order=order,
        status=RunStatus.COMPLETED,
        predictions=predictions,
        pre_review_predictions=pre_review_predictions,
        reported_final_status=reported,
        final_status=final_status,
        review_trigger_id=REVIEW_TRIGGER_ID if arm is ArchitectureArm.M2 else None,
        review_selected_ids=selected,
        review_changed_count=changed,
        static_coordinator_rule_id=(
            STATIC_COORDINATOR_RULE_ID
            if arm in {ArchitectureArm.M1, ArchitectureArm.M2}
            else None
        ),
        judgment_batch_count=judgment_batch_count,
        role_call_counts={role: counts[role] for role in _all_roles()},
        calls=tuple(records),
        trace=tuple(recorder.events),
    )


def _score(result: ArchitectureArmResult, pair: TrialGPTPair) -> ArchitectureScore | None:
    if result.status is not RunStatus.COMPLETED or result.final_status is None:
        return None
    predicted = {item.annotation_id: item for item in result.predictions}
    pre_review = {
        item.annotation_id: item for item in result.pre_review_predictions
    }
    has_complete_pre_review = (
        len(pre_review) == len(pair.criteria)
        and set(pre_review) == {row.annotation_id for row in pair.criteria}
    )
    tp = fp = fn = exact = expert_correct = public_correct = 0
    expert_nei = expert_nei_preserved = expert_decisive = decisive_to_nei = 0
    pre_review_correct = review_wrong_to_correct = review_correct_to_wrong = 0
    for row in pair.criteria:
        item = predicted[row.annotation_id]
        after_correct = item.eligibility_label == row.expert_eligibility
        expert_correct += after_correct
        public_correct += item.eligibility_label == row.gpt4_eligibility
        if has_complete_pre_review:
            before_correct = (
                pre_review[row.annotation_id].eligibility_label
                == row.expert_eligibility
            )
            pre_review_correct += before_correct
            review_wrong_to_correct += (not before_correct) and after_correct
            review_correct_to_wrong += before_correct and (not after_correct)
        predicted_evidence = set(item.evidence_sentence_ids)
        gold_evidence = set(row.expert_sentences)
        exact += predicted_evidence == gold_evidence
        tp += len(predicted_evidence & gold_evidence)
        fp += len(predicted_evidence - gold_evidence)
        fn += len(gold_evidence - predicted_evidence)
        if row.expert_eligibility == "not enough information":
            expert_nei += 1
            expert_nei_preserved += item.eligibility_label == "not enough information"
        else:
            expert_decisive += 1
            decisive_to_nei += item.eligibility_label == "not enough information"
    expert_final = aggregate_trial_status(
        [(row.criterion_type, row.expert_eligibility) for row in pair.criteria]
    )
    public_final = aggregate_trial_status(
        [(row.criterion_type, row.gpt4_eligibility) for row in pair.criteria]
    )
    return ArchitectureScore(
        criteria_total=len(pair.criteria),
        expert_label_correct=expert_correct,
        public_label_agreement=public_correct,
        evidence_exact=exact,
        evidence_true_positive=tp,
        evidence_false_positive=fp,
        evidence_false_negative=fn,
        expert_nei_total=expert_nei,
        expert_nei_preserved=expert_nei_preserved,
        expert_decisive_total=expert_decisive,
        decisive_to_nei=decisive_to_nei,
        expert_final_status=expert_final,
        public_final_status=public_final,
        expert_final_correct=result.final_status is expert_final,
        pre_review_expert_label_correct=(
            pre_review_correct if has_complete_pre_review else None
        ),
        review_wrong_to_correct=review_wrong_to_correct,
        review_correct_to_wrong=review_correct_to_wrong,
    )


def _rate(numerator: int, denominator: int) -> float:
    return 0.0 if denominator == 0 else numerator / denominator


def _arm_metrics(results: Sequence[ArchitectureArmResult], arm: ArchitectureArm) -> ArmAggregateMetrics:
    selected = [item for item in results if item.arm is arm and item.score is not None]
    scores = [item.score for item in selected if item.score is not None]
    criteria = sum(item.criteria_total for item in scores)
    tp = sum(item.evidence_true_positive for item in scores)
    fp = sum(item.evidence_false_positive for item in scores)
    fn = sum(item.evidence_false_negative for item in scores)
    f1_denominator = 2 * tp + fp + fn
    pre_review_scores = [
        item
        for item in scores
        if item.pre_review_expert_label_correct is not None
    ]
    pre_review_correct = sum(
        cast(int, item.pre_review_expert_label_correct)
        for item in pre_review_scores
    )
    pre_review_criteria = sum(item.criteria_total for item in pre_review_scores)
    pre_review_accuracy = (
        None
        if pre_review_criteria == 0
        else _rate(pre_review_correct, pre_review_criteria)
    )
    post_review_accuracy = (
        None
        if pre_review_criteria == 0
        else _rate(
            sum(item.expert_label_correct for item in pre_review_scores),
            pre_review_criteria,
        )
    )
    return ArmAggregateMetrics(
        arm=arm,
        cases=len(scores),
        criteria=criteria,
        criterion_accuracy=_rate(sum(item.expert_label_correct for item in scores), criteria),
        evidence_exact_rate=_rate(sum(item.evidence_exact for item in scores), criteria),
        evidence_micro_f1=1.0 if f1_denominator == 0 else (2 * tp) / f1_denominator,
        trial_status_accuracy=_rate(sum(item.expert_final_correct for item in scores), len(scores)),
        expert_nei_preservation=_rate(sum(item.expert_nei_preserved for item in scores), sum(item.expert_nei_total for item in scores)),
        decisive_to_nei_rate=_rate(sum(item.decisive_to_nei for item in scores), sum(item.expert_decisive_total for item in scores)),
        review_selected=sum(len(item.review_selected_ids) for item in selected),
        review_changed=sum(item.review_changed_count for item in selected),
        pre_review_criterion_accuracy=pre_review_accuracy,
        review_accuracy_delta=(
            None
            if pre_review_accuracy is None or post_review_accuracy is None
            else post_review_accuracy - pre_review_accuracy
        ),
        review_wrong_to_correct=sum(
            item.review_wrong_to_correct for item in pre_review_scores
        ),
        review_correct_to_wrong=sum(
            item.review_correct_to_wrong for item in pre_review_scores
        ),
    )


def _paired_metrics(
    results: Sequence[ArchitectureArmResult],
    pairs_by_case: Mapping[str, TrialGPTPair],
    arm_a: ArchitectureArm,
    arm_b: ArchitectureArm,
) -> PairedArmMetrics:
    completed = {
        (item.case_id, item.arm): item
        for item in results
        if item.status is RunStatus.COMPLETED and item.score is not None
    }
    shared = sorted(
        case_id
        for case_id in pairs_by_case
        if (case_id, arm_a) in completed and (case_id, arm_b) in completed
    )
    wrong_to_correct = correct_to_wrong = trial_wtc = trial_ctw = compared = 0
    for case_id in shared:
        first = completed[(case_id, arm_a)]
        second = completed[(case_id, arm_b)]
        first_by_id = {item.annotation_id: item for item in first.predictions}
        second_by_id = {item.annotation_id: item for item in second.predictions}
        pair = pairs_by_case[case_id]
        for row in pair.criteria:
            first_correct = first_by_id[row.annotation_id].eligibility_label == row.expert_eligibility
            second_correct = second_by_id[row.annotation_id].eligibility_label == row.expert_eligibility
            wrong_to_correct += (not first_correct) and second_correct
            correct_to_wrong += first_correct and (not second_correct)
            compared += 1
        first_trial = bool(first.score and first.score.expert_final_correct)
        second_trial = bool(second.score and second.score.expert_final_correct)
        trial_wtc += (not first_trial) and second_trial
        trial_ctw += first_trial and (not second_trial)
    return PairedArmMetrics(
        arm_a=arm_a,
        arm_b=arm_b,
        paired_cases=len(shared),
        criteria_compared=compared,
        wrong_to_correct=wrong_to_correct,
        correct_to_wrong=correct_to_wrong,
        trial_wrong_to_correct=trial_wtc,
        trial_correct_to_wrong=trial_ctw,
    )


def run_trialgpt_architecture_benchmark(
    pairs: Sequence[TrialGPTPair],
    model: StructuredModel,
    *,
    explicit_pair_ids: Sequence[str] | None = None,
    retrieval_top_k: int = 5,
    order_seed: int = ORDER_SEED,
    on_case_completed: CaseCompletedCallback | None = None,
) -> TrialGPTArchitectureBenchmark:
    """Run balanced S1/M1/M2 calls and reveal gold only in the scoring pass.

    ``on_case_completed`` is called immediately after a case's three arms, in
    the exact planned order and before gold scoring.  A caller can append these
    stable-ID results to JSONL and resume at the case boundary without making
    this provider-neutral module own filesystem policy.
    """

    chosen = (
        list(pairs)
        if explicit_pair_ids is None
        else select_architecture_pairs(pairs, explicit_pair_ids)
    )
    cases = [build_architecture_case(item, retrieval_top_k=retrieval_top_k) for item in chosen]
    case_by_id = {case.case_id: case for case in cases}
    plans = plan_architecture_arm_orders(cases, seed=order_seed)
    raw_results: list[ArchitectureArmResult] = []
    for plan in plans:
        case = case_by_id[plan.case_id]
        case_results = run_trialgpt_architecture_case(case, plan, model)
        raw_results.extend(case_results)
        if on_case_completed is not None:
            on_case_completed(plan, case_results)

    return assemble_trialgpt_architecture_benchmark(
        chosen,
        plans,
        raw_results,
        order_seed=order_seed,
        retrieval_top_k=retrieval_top_k,
    )


def run_trialgpt_architecture_case(
    case: TrialGPTArchitectureCase,
    plan: ArchitectureCasePlan,
    model: StructuredModel,
    *,
    prior_results: Sequence[ArchitectureArmResult] = (),
) -> tuple[ArchitectureArmResult, ArchitectureArmResult, ArchitectureArmResult]:
    """Run unfinished arms of one preplanned case and reuse completed arms."""

    if plan.case_id != case.case_id or plan.pair_id != case.pair_id:
        raise ValueError("case and architecture plan identifiers do not match")
    prior_by_arm: dict[ArchitectureArm, ArchitectureArmResult] = {}
    for item in prior_results:
        if item.arm in prior_by_arm:
            raise ValueError("prior results repeat an architecture arm")
        if (
            item.arm not in plan.arm_order
            or item.case_id != case.case_id
            or item.pair_id != case.pair_id
            or item.planned_arm_order != plan.arm_order
        ):
            raise ValueError("prior result does not match the case plan")
        prior_by_arm[item.arm] = item
    results = tuple(
        prior_by_arm[arm]
        if arm in prior_by_arm
        and prior_by_arm[arm].status is RunStatus.COMPLETED
        else _run_arm(case, arm, plan.arm_order, model)
        for arm in plan.arm_order
    )
    return cast(
        tuple[ArchitectureArmResult, ArchitectureArmResult, ArchitectureArmResult],
        results,
    )


def assemble_trialgpt_architecture_benchmark(
    pairs: Sequence[TrialGPTPair],
    plans: Sequence[ArchitectureCasePlan],
    raw_results: Sequence[ArchitectureArmResult],
    *,
    order_seed: int = ORDER_SEED,
    retrieval_top_k: int = 5,
) -> TrialGPTArchitectureBenchmark:
    """Score persisted gold-free case results after all model calls are done."""

    cases = [
        build_architecture_case(item, retrieval_top_k=retrieval_top_k)
        for item in pairs
    ]
    pair_by_case = {
        case.case_id: pair for case, pair in zip(cases, pairs, strict=True)
    }
    plan_by_case = {plan.case_id: plan for plan in plans}
    if len(pair_by_case) != len(pairs) or len(plan_by_case) != len(plans):
        raise ValueError("pairs and plans must have unique case identifiers")
    if set(pair_by_case) != set(plan_by_case):
        raise ValueError("plans must cover exactly the supplied patient-trial pairs")

    seen: set[tuple[str, ArchitectureArm]] = set()
    results_by_case: Counter[str] = Counter()
    for item in raw_results:
        key = (item.case_id, item.arm)
        if key in seen or item.case_id not in plan_by_case:
            raise ValueError("raw results repeat or reference an unknown case/arm")
        plan = plan_by_case[item.case_id]
        if item.pair_id != plan.pair_id or item.planned_arm_order != plan.arm_order:
            raise ValueError("raw result does not match its frozen case plan")
        seen.add(key)
        results_by_case[item.case_id] += 1
    if any(results_by_case[case_id] != len(ARMS) for case_id in pair_by_case):
        raise ValueError("each case must contain exactly three architecture arms")

    # Gold/public labels are first read here, after every model-visible call.
    results = tuple(
        item.model_copy(update={"score": _score(item, pair_by_case[item.case_id])})
        for item in raw_results
    )
    arm_role_counts: dict[str, dict[str, int]] = {}
    for arm in ARMS:
        arm_role_counts[arm.value] = {
            role: sum(
                item.role_call_counts.get(role, 0)
                for item in results
                if item.arm is arm
            )
            for role in _all_roles()
        }
    metrics = {arm.value: _arm_metrics(results, arm) for arm in ARMS}
    paired = tuple(
        _paired_metrics(results, pair_by_case, first, second)
        for first, second in (
            (ArchitectureArm.S1, ArchitectureArm.M1),
            (ArchitectureArm.M1, ArchitectureArm.M2),
            (ArchitectureArm.S1, ArchitectureArm.M2),
        )
    )
    return TrialGPTArchitectureBenchmark(
        protocol_id=STATIC_ARCHITECTURE_PROTOCOL_ID,
        static_coordinator_rule_id=STATIC_COORDINATOR_RULE_ID,
        order_seed=order_seed,
        plans=tuple(plans),
        results=results,
        arm_role_call_counts=arm_role_counts,
        arm_metrics=metrics,
        paired_metrics=paired,
    )
