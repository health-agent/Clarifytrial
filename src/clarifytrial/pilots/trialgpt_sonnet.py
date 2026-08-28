"""Sonnet cost and criterion-judgment pilot on public TrialGPT annotations."""

from __future__ import annotations

import json
import re
import statistics
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..datasets.trialgpt import CriterionType, TrialGPTCriterionRow, TrialGPTPair
from ..disclaimer import DEFAULT_MEDICAL_DISCLAIMER
from ..llm.base import ModelCall, ModelUsage, StructuredModel
from ..usage import UsageCostSummary, summarize_usage
from .trialgpt_metrics import compute_trialgpt_diagnostics


EligibilityLabel = Literal[
    "included",
    "not included",
    "excluded",
    "not excluded",
    "not enough information",
    "not applicable",
]

PROMPT_ID = "prompts/trialgpt_criterion_judge.md"
ROLE = "trialgpt_criterion_judge"
REVIEW_PROMPT_ID = "prompts/trialgpt_criterion_reviewer.md"
REVIEW_ROLE = "trialgpt_criterion_reviewer"

_DISCLAIMER = DEFAULT_MEDICAL_DISCLAIMER


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TrialGPTCriterionPrediction(_StrictModel):
    """One structured prediction returned by the model."""

    annotation_id: int = Field(ge=0)
    explanation: str = Field(min_length=1)
    evidence_sentence_ids: list[int]
    eligibility_label: EligibilityLabel

    @field_validator("evidence_sentence_ids")
    @classmethod
    def sentence_ids_are_valid(cls, value: list[int]) -> list[int]:
        if any(item < 0 for item in value):
            raise ValueError("evidence sentence IDs must be non-negative")
        if len(value) != len(set(value)):
            raise ValueError("evidence sentence IDs must be unique")
        return value


class TrialGPTPredictionBatch(_StrictModel):
    """All criterion predictions for one patient, trial, and criterion type."""

    judgments: list[TrialGPTCriterionPrediction] = Field(min_length=1)

    @field_validator("judgments")
    @classmethod
    def annotation_ids_are_unique(
        cls, value: list[TrialGPTCriterionPrediction]
    ) -> list[TrialGPTCriterionPrediction]:
        identifiers = [item.annotation_id for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("judgments must not repeat annotation_id")
        return value


ReviewReason = Literal[
    "keep_insufficient",
    "direct_contradiction",
    "expected_documentation_absence",
    "strong_implicit_evidence",
]


class TrialGPTCriterionReview(_StrictModel):
    """One bounded reconsideration of an initial insufficient-information label."""

    annotation_id: int = Field(ge=0)
    review_reason: ReviewReason
    explanation: str = Field(min_length=1)
    evidence_sentence_ids: list[int]
    eligibility_label: EligibilityLabel

    @field_validator("evidence_sentence_ids")
    @classmethod
    def review_sentence_ids_are_valid(cls, value: list[int]) -> list[int]:
        return TrialGPTCriterionPrediction.sentence_ids_are_valid(value)

    @model_validator(mode="after")
    def reason_matches_label(self) -> "TrialGPTCriterionReview":
        remains_insufficient = self.eligibility_label == "not enough information"
        if remains_insufficient != (self.review_reason == "keep_insufficient"):
            raise ValueError(
                "keep_insufficient must retain not enough information, and a "
                "decisive review reason must change it"
            )
        return self


class TrialGPTReviewBatch(_StrictModel):
    """All requested NEI reviews for one patient, trial, and criterion type."""

    reviews: list[TrialGPTCriterionReview] = Field(min_length=1)

    @field_validator("reviews")
    @classmethod
    def review_ids_are_unique(
        cls, value: list[TrialGPTCriterionReview]
    ) -> list[TrialGPTCriterionReview]:
        identifiers = [item.annotation_id for item in value]
        if len(identifiers) != len(set(identifiers)):
            raise ValueError("reviews must not repeat annotation_id")
        return value


class TrialGPTMetricSet(_StrictModel):
    """Criterion-level label and cited-sentence agreement."""

    total: int = Field(ge=0)
    completed: int = Field(ge=0)
    label_correct: int = Field(ge=0)
    label_accuracy: float = Field(ge=0, le=1)
    evidence_exact: int = Field(ge=0)
    evidence_exact_rate: float = Field(ge=0, le=1)
    evidence_micro_f1: float = Field(ge=0, le=1)


class TrialGPTPilotSummary(_StrictModel):
    """Compact, reproducible result for one cost pilot."""

    run_kind: Literal["trialgpt_criterion_cost_pilot"]
    variant: str = "current"
    prompt_id: str = PROMPT_ID
    review_prompt_id: str | None = None
    model_id: str
    effort: str
    patient_trial_pairs: int = Field(ge=0)
    selection_seed: int | None = None
    sample_pair_ids: list[str]
    criterion_rows: int = Field(ge=0)
    expected_calls: int = Field(ge=0)
    initial_calls: int = Field(default=0, ge=0)
    review_calls: int = Field(default=0, ge=0)
    completed_calls: int = Field(ge=0)
    failed_calls: int = Field(ge=0)
    sample_category_counts: dict[str, int]
    initial_vs_expert: TrialGPTMetricSet | None = None
    sonnet_vs_expert: TrialGPTMetricSet
    public_trialgpt_vs_expert: TrialGPTMetricSet
    diagnostics: dict[str, Any] = Field(default_factory=dict)
    usage: UsageCostSummary
    thinking_tokens: int = Field(ge=0)
    latency_ms_median: float = Field(ge=0)
    latency_ms_p95: float = Field(ge=0)
    rough_full_105_pair_standard_cost_usd: float = Field(ge=0)
    rough_full_105_pair_batch_cost_usd: float = Field(ge=0)
    disclaimer: str
    limitations: list[str]


def _allowed_labels(criterion_type: CriterionType) -> list[str]:
    if criterion_type == "inclusion":
        return [
            "included",
            "not included",
            "not enough information",
            "not applicable",
        ]
    return [
        "excluded",
        "not excluded",
        "not enough information",
        "not applicable",
    ]


def build_trialgpt_payload(
    pair: TrialGPTPair,
    criterion_type: CriterionType,
) -> dict[str, Any]:
    """Build the model-visible input; public baseline and expert labels stay out."""

    criteria = [
        {
            "annotation_id": row.annotation_id,
            "criterion_text": row.criterion_text,
        }
        for row in pair.criteria
        if row.criterion_type == criterion_type and row.criterion_text is not None
    ]
    if not criteria:
        raise ValueError(f"pair has no usable {criterion_type} criteria")
    metadata = pair.metadata
    return {
        "patient_id": pair.patient_id,
        "trial_id": pair.trial_id,
        "patient_note_with_sentence_ids": pair.note,
        "trial": {
            "title": pair.trial_title if metadata is None else metadata.brief_title,
            "target_diseases": [] if metadata is None else metadata.diseases_list,
            "interventions": [] if metadata is None else metadata.drugs_list,
            "summary": "" if metadata is None else metadata.brief_summary,
        },
        "criterion_type": criterion_type,
        "allowed_labels": _allowed_labels(criterion_type),
        "criteria": criteria,
    }


def build_trialgpt_review_payload(
    pair: TrialGPTPair,
    criterion_type: CriterionType,
    initial_predictions: Sequence[TrialGPTCriterionPrediction],
) -> dict[str, Any]:
    """Build a gold-free payload containing only initial NEI judgments."""

    initial_by_id = {
        prediction.annotation_id: prediction
        for prediction in initial_predictions
        if prediction.eligibility_label == "not enough information"
    }
    rows = [
        row
        for row in pair.criteria
        if row.criterion_type == criterion_type
        and row.criterion_text is not None
        and row.annotation_id in initial_by_id
    ]
    if not rows:
        raise ValueError("review payload requires at least one initial NEI judgment")

    base = build_trialgpt_payload(pair, criterion_type)
    base["criteria"] = [
        {
            "annotation_id": row.annotation_id,
            "criterion_text": row.criterion_text,
            "initial_judgment": initial_by_id[row.annotation_id].model_dump(mode="json"),
        }
        for row in rows
    ]
    return base


def _validate_batch(
    batch: TrialGPTPredictionBatch,
    rows: Sequence[TrialGPTCriterionRow],
    criterion_type: CriterionType,
) -> None:
    expected = {row.annotation_id for row in rows if row.criterion_text is not None}
    actual = {item.annotation_id for item in batch.judgments}
    if actual != expected:
        raise ValueError("model output did not cover exactly the supplied annotation IDs")
    allowed = set(_allowed_labels(criterion_type))
    if any(item.eligibility_label not in allowed for item in batch.judgments):
        raise ValueError("model output used a label from the wrong criterion type")
    valid_sentence_ids = _note_sentence_ids(rows[0].note)
    if any(
        not set(item.evidence_sentence_ids) <= valid_sentence_ids
        for item in batch.judgments
    ):
        raise ValueError("model output cited a sentence ID absent from the patient note")


def _note_sentence_ids(note: str) -> set[int]:
    identifiers = {
        int(match.group(1))
        for line in note.splitlines()
        if (match := re.match(r"^\s*(\d+)\.", line)) is not None
    }
    if not identifiers:
        raise ValueError("patient note has no numbered sentence IDs")
    return identifiers


def _validate_review_batch(
    batch: TrialGPTReviewBatch,
    initial_predictions: Sequence[TrialGPTCriterionPrediction],
    criterion_type: CriterionType,
    note: str,
) -> None:
    expected = {
        item.annotation_id
        for item in initial_predictions
        if item.eligibility_label == "not enough information"
    }
    actual = {item.annotation_id for item in batch.reviews}
    if actual != expected:
        raise ValueError("review output did not cover exactly the supplied NEI IDs")
    allowed = set(_allowed_labels(criterion_type))
    if any(item.eligibility_label not in allowed for item in batch.reviews):
        raise ValueError("review output used a label from the wrong criterion type")
    valid_sentence_ids = _note_sentence_ids(note)
    if any(
        not set(item.evidence_sentence_ids) <= valid_sentence_ids
        for item in batch.reviews
    ):
        raise ValueError("review output cited a sentence ID absent from the patient note")


def _evidence_f1(
    predictions: Mapping[int, set[int]],
    gold: Mapping[int, set[int]],
) -> float:
    true_positive = false_positive = false_negative = 0
    for annotation_id, expected in gold.items():
        predicted = predictions.get(annotation_id, set())
        true_positive += len(predicted & expected)
        false_positive += len(predicted - expected)
        false_negative += len(expected - predicted)
    denominator = 2 * true_positive + false_positive + false_negative
    return 1.0 if denominator == 0 else (2 * true_positive) / denominator


def _metric_set(
    rows: Sequence[TrialGPTCriterionRow],
    labels: Mapping[int, str],
    evidence: Mapping[int, set[int]],
) -> TrialGPTMetricSet:
    total = len(rows)
    completed = sum(row.annotation_id in labels for row in rows)
    correct = sum(
        labels.get(row.annotation_id) == row.expert_eligibility for row in rows
    )
    evidence_gold = {
        row.annotation_id: set(row.expert_sentences) for row in rows
    }
    exact = sum(
        evidence.get(row.annotation_id, set()) == evidence_gold[row.annotation_id]
        for row in rows
        if row.annotation_id in labels
    )
    return TrialGPTMetricSet(
        total=total,
        completed=completed,
        label_correct=correct,
        label_accuracy=0.0 if total == 0 else correct / total,
        evidence_exact=exact,
        evidence_exact_rate=0.0 if total == 0 else exact / total,
        evidence_micro_f1=_evidence_f1(evidence, evidence_gold),
    )


def _percentile_95(values: Sequence[int]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, int(0.95 * len(ordered) + 0.999999) - 1))
    return float(ordered[index])


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def run_trialgpt_pilot(
    pairs: Sequence[TrialGPTPair],
    model: StructuredModel,
    output_dir: str | Path,
    *,
    configured_model_id: str = "claude-sonnet-5",
    effort: str = "medium",
    selection_seed: int | None = None,
    variant: str = "current",
    prompt_id: str = PROMPT_ID,
    review_prompt_id: str | None = None,
) -> TrialGPTPilotSummary:
    """Run bundled criterion calls and an optional bounded NEI-only review."""

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    all_rows = [row for pair in pairs for row in pair.criteria if row.criterion_text]
    initial_predictions: dict[int, TrialGPTCriterionPrediction] = {}
    final_predictions: dict[int, TrialGPTCriterionPrediction] = {}
    reviews: dict[int, TrialGPTCriterionReview] = {}
    usage_items: list[ModelUsage] = []
    call_rows: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    successful_bundles: list[
        tuple[
            TrialGPTPair,
            CriterionType,
            list[TrialGPTCriterionRow],
            list[TrialGPTCriterionPrediction],
        ]
    ] = []

    expected_initial_calls = 0
    completed_initial_calls = 0
    for pair in pairs:
        for criterion_type in ("inclusion", "exclusion"):
            typed_rows = [
                row
                for row in pair.criteria
                if row.criterion_type == criterion_type and row.criterion_text
            ]
            if not typed_rows:
                continue
            expected_initial_calls += 1
            payload = build_trialgpt_payload(pair, criterion_type)
            try:
                batch, usage = model.complete(
                    ModelCall(
                        role=ROLE,
                        prompt_id=prompt_id,
                        payload=payload,
                        response_model=TrialGPTPredictionBatch,
                    )
                )
                _validate_batch(batch, typed_rows, criterion_type)
            except Exception as exc:  # failure is retained instead of gold-filled
                failures.append(
                    {
                        "patient_id": pair.patient_id,
                        "trial_id": pair.trial_id,
                        "criterion_type": criterion_type,
                        "stage": "initial",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                continue
            completed_initial_calls += 1
            usage_items.append(usage)
            for prediction in batch.judgments:
                initial_predictions[prediction.annotation_id] = prediction
                final_predictions[prediction.annotation_id] = prediction
            successful_bundles.append(
                (pair, criterion_type, typed_rows, list(batch.judgments))
            )
            call_rows.append(
                {
                    "patient_id": pair.patient_id,
                    "trial_id": pair.trial_id,
                    "criterion_type": criterion_type,
                    "stage": "initial",
                    "prompt_id": prompt_id,
                    "criterion_count": len(typed_rows),
                    "usage": asdict(usage),
                }
            )

    expected_review_calls = 0
    completed_review_calls = 0
    if review_prompt_id is not None:
        for pair, criterion_type, _, bundle_predictions in successful_bundles:
            initial_nei = [
                item
                for item in bundle_predictions
                if item.eligibility_label == "not enough information"
            ]
            if not initial_nei:
                continue
            expected_review_calls += 1
            payload = build_trialgpt_review_payload(
                pair,
                criterion_type,
                initial_nei,
            )
            try:
                batch, usage = model.complete(
                    ModelCall(
                        role=REVIEW_ROLE,
                        prompt_id=review_prompt_id,
                        payload=payload,
                        response_model=TrialGPTReviewBatch,
                    )
                )
                _validate_review_batch(batch, initial_nei, criterion_type, pair.note)
            except Exception as exc:  # initial predictions remain available
                failures.append(
                    {
                        "patient_id": pair.patient_id,
                        "trial_id": pair.trial_id,
                        "criterion_type": criterion_type,
                        "stage": "review",
                        "error_type": type(exc).__name__,
                        "error": str(exc),
                    }
                )
                continue
            completed_review_calls += 1
            usage_items.append(usage)
            for review in batch.reviews:
                reviews[review.annotation_id] = review
                final_predictions[review.annotation_id] = TrialGPTCriterionPrediction(
                    annotation_id=review.annotation_id,
                    explanation=review.explanation,
                    evidence_sentence_ids=review.evidence_sentence_ids,
                    eligibility_label=review.eligibility_label,
                )
            call_rows.append(
                {
                    "patient_id": pair.patient_id,
                    "trial_id": pair.trial_id,
                    "criterion_type": criterion_type,
                    "stage": "review",
                    "prompt_id": review_prompt_id,
                    "criterion_count": len(initial_nei),
                    "usage": asdict(usage),
                }
            )

    # Gold and the published GPT-4 outputs are used only after every model call.
    initial_labels = {
        key: value.eligibility_label for key, value in initial_predictions.items()
    }
    initial_evidence = {
        key: set(value.evidence_sentence_ids)
        for key, value in initial_predictions.items()
    }
    sonnet_labels = {
        key: value.eligibility_label for key, value in final_predictions.items()
    }
    sonnet_evidence = {
        key: set(value.evidence_sentence_ids)
        for key, value in final_predictions.items()
    }
    trialgpt_labels = {row.annotation_id: row.gpt4_eligibility for row in all_rows}
    trialgpt_evidence = {
        row.annotation_id: set(row.gpt4_sentences) for row in all_rows
    }

    prediction_path = destination / "predictions.jsonl"
    with prediction_path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in sorted(all_rows, key=lambda item: item.annotation_id):
            initial_prediction = initial_predictions.get(row.annotation_id)
            prediction = final_predictions.get(row.annotation_id)
            review = reviews.get(row.annotation_id)
            record = {
                "annotation_id": row.annotation_id,
                "patient_id": row.patient_id,
                "trial_id": row.trial_id,
                "criterion_type": row.criterion_type,
                "initial_sonnet": (
                    None
                    if initial_prediction is None
                    else initial_prediction.model_dump(mode="json")
                ),
                "review": None if review is None else review.model_dump(mode="json"),
                "sonnet": None if prediction is None else prediction.model_dump(mode="json"),
                "public_trialgpt_label": row.gpt4_eligibility,
                "public_trialgpt_evidence_sentence_ids": row.gpt4_sentences,
                "expert_label": row.expert_eligibility,
                "expert_evidence_sentence_ids": row.expert_sentences,
            }
            stream.write(json.dumps(record, ensure_ascii=False))
            stream.write("\n")

    with (destination / "calls.jsonl").open(
        "w", encoding="utf-8", newline="\n"
    ) as stream:
        for record in call_rows:
            stream.write(json.dumps(record, ensure_ascii=False))
            stream.write("\n")
    _write_json(destination / "failures.json", {"failures": failures})

    usage_summary = summarize_usage(usage_items)
    latencies = [item.latency_ms for item in usage_items if item.latency_ms is not None]
    thinking_tokens = sum(item.thinking_tokens or 0 for item in usage_items)
    pair_count = len(pairs)
    projection_factor = 0.0 if pair_count == 0 else 105 / pair_count
    normalized_diagnostics = [
        {
            "criterion_type": row.criterion_type,
            "expert_label": row.expert_eligibility,
            "public_trialgpt_label": row.gpt4_eligibility,
            "predicted_label": final_predictions[row.annotation_id].eligibility_label,
            "expert_evidence_ids": row.expert_sentences,
            "predicted_evidence_ids": final_predictions[
                row.annotation_id
            ].evidence_sentence_ids,
        }
        for row in all_rows
        if row.annotation_id in final_predictions
    ]
    diagnostics = compute_trialgpt_diagnostics(normalized_diagnostics)
    diagnostics["training_flag_is_not_a_split"] = {
        str(flag).lower(): {
            "rows": len(subset),
            "public_label_matches_expert": sum(
                row.gpt4_eligibility == row.expert_eligibility for row in subset
            ),
            "predicted_label_matches_expert": sum(
                final_predictions.get(row.annotation_id) is not None
                and final_predictions[row.annotation_id].eligibility_label
                == row.expert_eligibility
                for row in subset
            ),
        }
        for flag in (True, False)
        if (subset := [row for row in all_rows if row.training is flag])
    }
    summary = TrialGPTPilotSummary(
        run_kind="trialgpt_criterion_cost_pilot",
        variant=variant,
        prompt_id=prompt_id,
        review_prompt_id=review_prompt_id,
        model_id=(usage_items[0].model_id if usage_items else configured_model_id),
        effort=effort,
        patient_trial_pairs=pair_count,
        selection_seed=selection_seed,
        sample_pair_ids=[f"{pair.patient_id}/{pair.trial_id}" for pair in pairs],
        criterion_rows=len(all_rows),
        expected_calls=expected_initial_calls + expected_review_calls,
        initial_calls=completed_initial_calls,
        review_calls=completed_review_calls,
        completed_calls=len(usage_items),
        failed_calls=len(failures),
        sample_category_counts=dict(sorted(Counter(pair.category for pair in pairs).items())),
        initial_vs_expert=_metric_set(
            all_rows,
            initial_labels,
            initial_evidence,
        ),
        sonnet_vs_expert=_metric_set(all_rows, sonnet_labels, sonnet_evidence),
        public_trialgpt_vs_expert=_metric_set(
            all_rows, trialgpt_labels, trialgpt_evidence
        ),
        diagnostics=diagnostics,
        usage=usage_summary,
        thinking_tokens=thinking_tokens,
        latency_ms_median=0.0 if not latencies else float(statistics.median(latencies)),
        latency_ms_p95=_percentile_95(latencies),
        rough_full_105_pair_standard_cost_usd=(
            usage_summary.total_cost_usd * projection_factor
        ),
        rough_full_105_pair_batch_cost_usd=(
            usage_summary.total_cost_usd * projection_factor * 0.5
        ),
        disclaimer=_DISCLAIMER,
        limitations=[
            "This 20-pair sample is a cost and integration pilot, not a final "
            "performance estimate.",
            "The public training flag is not a held-out split and its semantics "
            "are undocumented in the dataset card; in this dataset it nearly "
            "mirrors agreement between the fixed public output and expert label.",
            "Public TrialGPT values are fixed published outputs, not an "
            "independently rerun comparator.",
            "TrialGPT annotations do not contain the v5 next-action or "
            "evidence-sufficiency gold labels.",
        ],
    )
    _write_json(destination / "summary.json", summary.model_dump(mode="json"))
    return summary
