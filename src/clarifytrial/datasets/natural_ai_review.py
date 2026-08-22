"""Two-pass model review for preliminary natural-evaluation criterion labels."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..contracts import ComparisonOperator, CriterionKind, NumericConstraint
from ..llm import ModelCall, ModelUsage, StructuredModel
from ..preparation.contracts import TrialCriterionDraft
from ..preparation.source_matching import SourceValidationError
from ..preparation.structured_value_validation import validate_trial_criterion_source
from .integrity import portable_text_sha256
from .natural_evaluation import load_natural_evaluation_selection_config


FIRST_PASS_PROMPT = "prompts/natural_criterion_ai_review.md"
AUDIT_PASS_PROMPT = "prompts/natural_criterion_ai_audit.md"
ALLOWED_EXPECTED_VALUES = frozenset(
    {
        "present",
        "absent",
        "positive",
        "negative",
        "diagnosed",
        "not_diagnosed",
        "true",
        "false",
    }
)


class _ReviewModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class AiObjectiveAnnotation(_ReviewModel):
    fact_code: str = Field(pattern=r"^[a-z0-9_]+$")
    fact_description: str = Field(min_length=1)
    criterion_summary: str = Field(min_length=1)
    expected_value: str | None = Field(
        default=None,
        pattern=r"^[a-z0-9_]+$",
    )
    operator: Literal["gt", "gte", "lt", "lte", "eq"] | None = None
    threshold: float | None = Field(default=None, allow_inf_nan=False)
    unit: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def one_testable_form_is_present(self) -> "AiObjectiveAnnotation":
        numeric = (
            self.operator is not None,
            self.threshold is not None,
            self.unit is not None,
        )
        if any(numeric) and not all(numeric):
            raise ValueError("numeric operator, threshold, and unit belong together")
        if all(numeric) == (self.expected_value is not None):
            raise ValueError("use either a numeric rule or an expected state")
        return self


AiDecision = Literal["include", "exclude", "uncertain"]
AiConfidence = Literal["high", "medium", "low"]
NaturalEvaluationSourceSection = Literal["trials", "reserve_trials"]
AiReasonCode = Literal[
    "objective_numeric",
    "objective_temporal",
    "objective_explicit_state",
    "heading_or_context",
    "subjective_judgment",
    "complex_clinical_interpretation",
    "conditional_logic",
    "incomplete_fragment",
    "other",
]


class AiCriterionLineReview(_ReviewModel):
    candidate_id: str = Field(min_length=1)
    decision: AiDecision
    confidence: AiConfidence
    reason_code: AiReasonCode
    annotations: list[AiObjectiveAnnotation]
    note: str | None = None

    @model_validator(mode="after")
    def decision_matches_annotations(self) -> "AiCriterionLineReview":
        if self.decision == "include" and not self.annotations:
            raise ValueError("included lines require at least one annotation")
        if self.decision != "include" and self.annotations:
            raise ValueError("excluded or uncertain lines cannot carry annotations")
        if self.decision == "include" and self.confidence == "low":
            raise ValueError("low-confidence lines must be uncertain")
        return self


class AiCriterionReviewBatch(_ReviewModel):
    reviews: list[AiCriterionLineReview] = Field(min_length=1)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _usage_dict(usage: ModelUsage) -> dict[str, Any]:
    values = asdict(usage)
    return {key: value for key, value in values.items() if value is not None}


def _trial_payload(trial: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "trial": {
            "group_id": trial["group_id"],
            "nct_id": trial["nct_id"],
            "title": trial["title"],
            "conditions": trial["conditions"],
        },
        "source_lines": [
            {
                "candidate_id": candidate["candidate_id"],
                "section": candidate["section_hint"],
                "source_text": candidate["source_text"],
            }
            for candidate in trial["criterion_candidates"]
        ],
    }


def _selected_source_trials(
    payload: Mapping[str, Any],
    *,
    source_section: NaturalEvaluationSourceSection,
    group_ids: Sequence[str] | None,
) -> tuple[list[Mapping[str, Any]], list[str]]:
    raw_trials = payload.get(source_section)
    if not isinstance(raw_trials, list) or not raw_trials:
        raise ValueError(f"source review has no trials in {source_section}")
    available_groups = list(dict.fromkeys(str(row["group_id"]) for row in raw_trials))
    selected_groups = list(group_ids or available_groups)
    if not selected_groups:
        raise ValueError("at least one disease group is required")
    if len(selected_groups) != len(set(selected_groups)):
        raise ValueError("disease groups must not repeat")
    unknown = set(selected_groups) - set(available_groups)
    if unknown:
        raise ValueError(
            "source review does not contain disease groups: "
            + ", ".join(sorted(unknown))
        )
    selected = [
        row for row in raw_trials if str(row["group_id"]) in selected_groups
    ]
    return selected, selected_groups


def validate_ai_review_batch(
    trial: Mapping[str, Any],
    batch: AiCriterionReviewBatch,
) -> AiCriterionReviewBatch:
    """Check source coverage and reject unsupported numeric values."""

    candidates = {
        item["candidate_id"]: item for item in trial["criterion_candidates"]
    }
    returned_ids = [item.candidate_id for item in batch.reviews]
    expected_ids = list(candidates)
    if returned_ids != expected_ids:
        raise ValueError("model review must cover source candidates in order")
    if len(returned_ids) != len(set(returned_ids)):
        raise ValueError("model review repeated a candidate ID")

    validated_reviews = []
    for review in batch.reviews:
        source = candidates[review.candidate_id]
        section = CriterionKind(str(source["section_hint"]))
        unsupported_state = any(
            annotation.expected_value is not None
            and annotation.expected_value not in ALLOWED_EXPECTED_VALUES
            for annotation in review.annotations
        )
        if unsupported_state:
            validated_reviews.append(
                review.model_copy(
                    update={
                        "decision": "uncertain",
                        "confidence": "low",
                        "reason_code": "conditional_logic",
                        "annotations": [],
                        "note": "categorical state is outside the supported pilot format",
                    }
                )
            )
            continue
        try:
            for annotation in review.annotations:
                if annotation.operator is None:
                    continue
                draft = TrialCriterionDraft(
                    kind=section,
                    statement=annotation.criterion_summary,
                    source_quote=str(source["source_text"]),
                    numeric_constraint=NumericConstraint(
                        concept=annotation.fact_code,
                        operator=ComparisonOperator(annotation.operator),
                        threshold=float(annotation.threshold),
                        unit=str(annotation.unit),
                    ),
                )
                validate_trial_criterion_source(draft, str(source["source_text"]))
        except SourceValidationError:
            review = review.model_copy(
                update={
                    "decision": "uncertain",
                    "confidence": "low",
                    "reason_code": "other",
                    "annotations": [],
                    "note": "numeric fields did not pass the source-value check",
                }
            )
        validated_reviews.append(review)
    return AiCriterionReviewBatch(reviews=validated_reviews)


def _run_one_review(
    *,
    model: StructuredModel,
    trial: Mapping[str, Any],
    prompt_id: str,
    first_pass: AiCriterionReviewBatch | None,
) -> tuple[AiCriterionReviewBatch, ModelUsage]:
    payload = _trial_payload(trial)
    if first_pass is not None:
        payload["first_pass_draft"] = first_pass.model_dump(mode="json")
    response, usage = model.complete(
        ModelCall(
            role=(
                "natural_criterion_ai_review"
                if first_pass is None
                else "natural_criterion_ai_audit"
            ),
            prompt_id=prompt_id,
            payload=payload,
            response_model=AiCriterionReviewBatch,
        )
    )
    return validate_ai_review_batch(trial, response), usage


def _failed_review_batch(
    trial: Mapping[str, Any],
    *,
    note: str,
) -> AiCriterionReviewBatch:
    return AiCriterionReviewBatch(
        reviews=[
            AiCriterionLineReview(
                candidate_id=str(candidate["candidate_id"]),
                decision="uncertain",
                confidence="low",
                reason_code="other",
                annotations=[],
                note=note,
            )
            for candidate in trial["criterion_candidates"]
        ]
    )


def _run_pass(
    *,
    trials: Sequence[Mapping[str, Any]],
    model: StructuredModel,
    prompt_id: str,
    prior: Mapping[str, AiCriterionReviewBatch] | None,
    checkpoint_dir: Path,
    concurrency: int,
    progress: Callable[[str], None],
    chunk_size: int = 12,
) -> tuple[dict[str, AiCriterionReviewBatch], list[dict[str, Any]]]:
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    results: dict[str, AiCriterionReviewBatch] = {}
    usage_rows: list[dict[str, Any]] = []
    pending_chunks: list[
        tuple[str, str, Mapping[str, Any], AiCriterionReviewBatch | None]
    ] = []
    completed_chunks: dict[str, dict[int, AiCriterionReviewBatch]] = {}
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = Path(__file__).resolve().parents[3] / prompt_id
    prompt_sha256 = portable_text_sha256(prompt_path)

    def checkpoint_hash(
        trial: Mapping[str, Any],
        prior_batch: AiCriterionReviewBatch | None,
    ) -> str:
        value = _trial_payload(trial)
        if prior_batch is not None:
            value["first_pass_draft"] = prior_batch.model_dump(mode="json")
        return hashlib.sha256(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()

    for trial in trials:
        nct_id = str(trial["nct_id"])
        full_checkpoint = checkpoint_dir / f"{nct_id}.json"
        if full_checkpoint.exists():
            stored = json.loads(full_checkpoint.read_text(encoding="utf-8"))
            expected_hash = checkpoint_hash(
                trial,
                None if prior is None else prior[nct_id],
            )
            if (
                stored.get("prompt_id") != prompt_id
                or stored.get("prompt_sha256") != prompt_sha256
                or stored.get("trial_payload_sha256") != expected_hash
            ):
                raise ValueError(f"stale AI review checkpoint for {nct_id}")
            batch = validate_ai_review_batch(
                trial,
                AiCriterionReviewBatch.model_validate(stored["response"]),
            )
            results[nct_id] = batch
            usage_rows.append(dict(stored["usage"]))
            progress(f"reused {nct_id} from {full_checkpoint}")
            continue
        candidates = list(trial["criterion_candidates"])
        completed_chunks[nct_id] = {}
        for chunk_index, start in enumerate(range(0, len(candidates), chunk_size), 1):
            chunk_trial = {
                **trial,
                "criterion_candidates": candidates[start : start + chunk_size],
            }
            candidate_ids = {
                item["candidate_id"] for item in chunk_trial["criterion_candidates"]
            }
            prior_batch = None
            if prior is not None:
                prior_batch = AiCriterionReviewBatch(
                    reviews=[
                        item
                        for item in prior[nct_id].reviews
                        if item.candidate_id in candidate_ids
                    ]
                )
            chunk_key = f"{nct_id}-part-{chunk_index:02d}"
            checkpoint = checkpoint_dir / f"{chunk_key}.json"
            if not checkpoint.exists():
                pending_chunks.append(
                    (chunk_key, nct_id, chunk_trial, prior_batch)
                )
                continue
            stored = json.loads(checkpoint.read_text(encoding="utf-8"))
            expected_hash = checkpoint_hash(chunk_trial, prior_batch)
            if (
                stored.get("prompt_id") != prompt_id
                or stored.get("prompt_sha256") != prompt_sha256
                or stored.get("trial_payload_sha256") != expected_hash
            ):
                raise ValueError(f"stale AI review checkpoint for {chunk_key}")
            batch = validate_ai_review_batch(
                chunk_trial,
                AiCriterionReviewBatch.model_validate(stored["response"]),
            )
            completed_chunks[nct_id][chunk_index] = batch
            usage_rows.append(dict(stored["usage"]))
            progress(f"reused {chunk_key} from {checkpoint}")

    def operation(
        item: tuple[str, str, Mapping[str, Any], AiCriterionReviewBatch | None]
    ):
        chunk_key, nct_id, trial, prior_batch = item
        batch, usage = _run_one_review(
            model=model,
            trial=trial,
            prompt_id=prompt_id,
            first_pass=prior_batch,
        )
        return chunk_key, nct_id, trial, prior_batch, batch, usage

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(operation, item): item for item in pending_chunks
        }
        for future in as_completed(futures):
            pending_item = futures[future]
            chunk_key, nct_id, trial, prior_batch = pending_item
            try:
                _, _, _, _, batch, usage = future.result()
                usage_row = {
                    "nct_id": nct_id,
                    "chunk_id": chunk_key,
                    "failed": False,
                    **_usage_dict(usage),
                }
                progress(f"reviewed {chunk_key} with {prompt_id}")
            except Exception as exc:
                batch = (
                    prior_batch
                    if prior_batch is not None
                    else _failed_review_batch(
                        trial,
                        note=(
                            "model output failed validation; requires "
                            "maximum-effort review"
                        ),
                    )
                )
                usage_row = {
                    "nct_id": nct_id,
                    "chunk_id": chunk_key,
                    "failed": True,
                    "error_type": type(exc).__name__,
                }
                progress(f"deferred {chunk_key}: {type(exc).__name__}")
            chunk_index = int(chunk_key.rsplit("-", 1)[1])
            completed_chunks[nct_id][chunk_index] = batch
            usage_rows.append(usage_row)
            trial_hash = checkpoint_hash(trial, prior_batch)
            _write_json(
                checkpoint_dir / f"{chunk_key}.json",
                {
                    "prompt_id": prompt_id,
                    "prompt_sha256": prompt_sha256,
                    "trial_payload_sha256": trial_hash,
                    "response": batch.model_dump(mode="json"),
                    "usage": usage_row,
                },
            )

    for trial in trials:
        nct_id = str(trial["nct_id"])
        if nct_id in results:
            continue
        chunks = completed_chunks[nct_id]
        expected_chunk_count = (
            len(trial["criterion_candidates"]) + chunk_size - 1
        ) // chunk_size
        if set(chunks) != set(range(1, expected_chunk_count + 1)):
            raise RuntimeError(f"AI review chunks are incomplete for {nct_id}")
        combined = AiCriterionReviewBatch(
            reviews=[
                item
                for chunk_index in range(1, expected_chunk_count + 1)
                for item in chunks[chunk_index].reviews
            ]
        )
        results[nct_id] = validate_ai_review_batch(trial, combined)
    return results, usage_rows


def _sum_usage(rows: Sequence[Mapping[str, Any]]) -> dict[str, int]:
    keys = (
        "input_tokens",
        "output_tokens",
        "thinking_tokens",
        "total_tokens",
        "latency_ms",
        "attempts",
    )
    return {
        "model_calls": len(rows),
        "failed_model_calls": sum(bool(row.get("failed")) for row in rows),
        **{
            key: sum(
                int(row[key])
                for row in rows
                if isinstance(row.get(key), int)
            )
            for key in keys
        },
    }


def _review_changed(
    first: AiCriterionLineReview,
    final: AiCriterionLineReview,
) -> bool:
    return (
        first.decision != final.decision
        or first.annotations != final.annotations
    )


def _review_rows_and_gold(
    trials: Sequence[Mapping[str, Any]],
    batches: Mapping[str, AiCriterionReviewBatch],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source_by_id = {
        candidate["candidate_id"]: (trial, candidate)
        for trial in trials
        for candidate in trial["criterion_candidates"]
    }
    review_rows = []
    gold_rows = []
    for trial in trials:
        nct_id = str(trial["nct_id"])
        for item in batches[nct_id].reviews:
            trial_source, candidate = source_by_id[item.candidate_id]
            review_rows.append(
                {
                    "group_id": trial_source["group_id"],
                    "nct_id": trial_source["nct_id"],
                    "candidate_id": item.candidate_id,
                    "section": candidate["section_hint"],
                    "line_number": candidate["line_number"],
                    "start_char": candidate["start_char"],
                    "end_char": candidate["end_char"],
                    "source_text": candidate["source_text"],
                    **item.model_dump(mode="json", exclude={"candidate_id"}),
                }
            )
            if item.decision != "include":
                continue
            for index, annotation in enumerate(item.annotations, start=1):
                gold_rows.append(
                    {
                        "criterion_id": f"{item.candidate_id}:annotation:{index:02d}",
                        "group_id": trial_source["group_id"],
                        "nct_id": trial_source["nct_id"],
                        "kind": candidate["section_hint"],
                        "candidate_id": item.candidate_id,
                        "source_text": candidate["source_text"],
                        "line_number": candidate["line_number"],
                        "confidence": item.confidence,
                        **annotation.model_dump(mode="json"),
                    }
                )
    return review_rows, gold_rows


def run_natural_evaluation_ai_review(
    *,
    source_path: str | Path,
    review_output_path: str | Path,
    gold_output_path: str | Path,
    checkpoint_dir: str | Path,
    model: StructuredModel,
    model_id: str,
    effort: str,
    source_section: NaturalEvaluationSourceSection = "trials",
    group_ids: Sequence[str] | None = None,
    concurrency: int = 3,
    chunk_size: int = 6,
    progress: Callable[[str], None] = lambda _: None,
) -> dict[str, Any]:
    """Run two isolated passes and write a clearly preliminary AI review."""

    if concurrency < 1:
        raise ValueError("concurrency must be positive")
    if chunk_size < 1:
        raise ValueError("chunk_size must be positive")
    source = Path(source_path)
    review_output = Path(review_output_path)
    gold_output = Path(gold_output_path)
    if review_output.exists() or gold_output.exists():
        raise FileExistsError("AI review outputs already exist")
    payload = json.loads(source.read_text(encoding="utf-8"))
    trials, selected_groups = _selected_source_trials(
        payload,
        source_section=source_section,
        group_ids=group_ids,
    )

    first, first_usage = _run_pass(
        trials=trials,
        model=model,
        prompt_id=FIRST_PASS_PROMPT,
        prior=None,
        checkpoint_dir=Path(checkpoint_dir) / "first_pass",
        concurrency=concurrency,
        progress=progress,
        chunk_size=chunk_size,
    )
    final, final_usage = _run_pass(
        trials=trials,
        model=model,
        prompt_id=AUDIT_PASS_PROMPT,
        prior=first,
        checkpoint_dir=Path(checkpoint_dir) / "audit_pass",
        concurrency=concurrency,
        progress=progress,
        chunk_size=chunk_size,
    )

    changed_ids = []
    for trial in trials:
        nct_id = str(trial["nct_id"])
        first_by_id = {item.candidate_id: item for item in first[nct_id].reviews}
        for final_item in final[nct_id].reviews:
            if _review_changed(first_by_id[final_item.candidate_id], final_item):
                changed_ids.append(final_item.candidate_id)
    review_rows, gold_rows = _review_rows_and_gold(trials, final)

    decision_counts = Counter(item["decision"] for item in review_rows)
    reason_counts = Counter(item["reason_code"] for item in review_rows)
    usage_rows = [*first_usage, *final_usage]
    source_digest = portable_text_sha256(source)
    review_document = {
        "status": "preliminary_single_ai_double_pass",
        "authority": (
            "AI-generated research draft; not physician gold and not independent "
            "two-person consensus"
        ),
        "source_path": str(source_path),
        "source_sha256": source_digest,
        "source_section": source_section,
        "group_ids": selected_groups,
        "model": model_id,
        "effort": effort,
        "passes": 2,
        "chunk_size": chunk_size,
        "source_line_count": len(review_rows),
        "changed_after_audit_count": len(changed_ids),
        "changed_after_audit_candidate_ids": changed_ids,
        "decision_counts": dict(decision_counts),
        "reason_counts": dict(reason_counts),
        "usage": _sum_usage(usage_rows),
        "reviews": review_rows,
    }
    gold_document = {
        "status": "preliminary_single_ai_reviewed_gold",
        "authority": review_document["authority"],
        "source_sha256": source_digest,
        "source_section": source_section,
        "group_ids": selected_groups,
        "model": model_id,
        "effort": effort,
        "criterion_count": len(gold_rows),
        "criteria": gold_rows,
    }
    _write_json(review_output, review_document)
    _write_json(gold_output, gold_document)
    return {
        "review_output": str(review_output),
        "gold_output": str(gold_output),
        "source_line_count": len(review_rows),
        "criterion_count": len(gold_rows),
        "changed_after_audit_count": len(changed_ids),
        "decision_counts": dict(decision_counts),
        "usage": review_document["usage"],
    }


def run_natural_evaluation_max_resolution(
    *,
    source_path: str | Path,
    base_review_path: str | Path,
    review_output_path: str | Path,
    gold_output_path: str | Path,
    checkpoint_dir: str | Path,
    model: StructuredModel,
    model_id: str,
    effort: str = "max",
    source_section: NaturalEvaluationSourceSection | None = None,
    group_ids: Sequence[str] | None = None,
    concurrency: int = 3,
    chunk_size: int = 3,
    selection_mode: Literal["uncertain_or_medium", "included"] = (
        "uncertain_or_medium"
    ),
    progress: Callable[[str], None] = lambda _: None,
) -> dict[str, Any]:
    """Use maximum effort on the requested subset of source lines."""

    review_output = Path(review_output_path)
    gold_output = Path(gold_output_path)
    if review_output.exists() or gold_output.exists():
        raise FileExistsError("maximum-resolution outputs already exist")
    source_path = Path(source_path)
    source = json.loads(source_path.read_text(encoding="utf-8"))
    base_review_path = Path(base_review_path)
    base_document = json.loads(base_review_path.read_text(encoding="utf-8"))
    base_section = str(base_document.get("source_section", "trials"))
    resolved_section = source_section or base_section
    if resolved_section != base_section:
        raise ValueError("maximum review source section differs from the base review")
    base_groups = base_document.get("group_ids")
    resolved_groups = list(group_ids) if group_ids is not None else base_groups
    if group_ids is not None and base_groups is not None:
        if list(group_ids) != list(base_groups):
            raise ValueError("maximum review disease groups differ from the base review")
    trials, selected_groups = _selected_source_trials(
        source,
        source_section=resolved_section,
        group_ids=resolved_groups,
    )
    raw_by_id = {
        str(item["candidate_id"]): item for item in base_document.get("reviews", [])
    }
    base_batches: dict[str, AiCriterionReviewBatch] = {}
    selected_trials = []
    selected_prior: dict[str, AiCriterionReviewBatch] = {}
    for trial in trials:
        reviews = []
        selected_ids = set()
        for candidate in trial["criterion_candidates"]:
            candidate_id = str(candidate["candidate_id"])
            raw = raw_by_id.get(candidate_id)
            if raw is None:
                raise ValueError(f"base AI review is missing {candidate_id}")
            item = AiCriterionLineReview.model_validate(
                {
                    "candidate_id": candidate_id,
                    "decision": raw["decision"],
                    "confidence": raw["confidence"],
                    "reason_code": raw["reason_code"],
                    "annotations": raw["annotations"],
                    "note": raw.get("note"),
                }
            )
            reviews.append(item)
        validated = validate_ai_review_batch(
            trial,
            AiCriterionReviewBatch(reviews=reviews),
        )
        base_batches[str(trial["nct_id"])] = validated
        for item in validated.reviews:
            supported = (
                item.note
                != "categorical state is outside the supported pilot format"
            )
            selected = (
                item.decision == "include"
                if selection_mode == "included"
                else item.decision == "uncertain" or item.confidence == "medium"
            )
            if supported and selected:
                selected_ids.add(item.candidate_id)
        if not selected_ids:
            continue
        selected_trial = {
            **trial,
            "criterion_candidates": [
                item
                for item in trial["criterion_candidates"]
                if item["candidate_id"] in selected_ids
            ],
        }
        selected_trials.append(selected_trial)
        selected_prior[str(trial["nct_id"])] = AiCriterionReviewBatch(
            reviews=[
                item
                for item in validated.reviews
                if item.candidate_id in selected_ids
            ]
        )

    resolved, max_usage_rows = _run_pass(
        trials=selected_trials,
        model=model,
        prompt_id=AUDIT_PASS_PROMPT,
        prior=selected_prior,
        checkpoint_dir=Path(checkpoint_dir),
        concurrency=concurrency,
        progress=progress,
        chunk_size=chunk_size,
    )
    final_batches = dict(base_batches)
    changed_by_max = []
    for trial in selected_trials:
        nct_id = str(trial["nct_id"])
        replacements = {item.candidate_id: item for item in resolved[nct_id].reviews}
        merged = []
        for item in base_batches[nct_id].reviews:
            replacement = replacements.get(item.candidate_id, item)
            if replacement is not item and _review_changed(item, replacement):
                changed_by_max.append(item.candidate_id)
            merged.append(replacement)
        full_trial = next(item for item in trials if str(item["nct_id"]) == nct_id)
        final_batches[nct_id] = validate_ai_review_batch(
            full_trial,
            AiCriterionReviewBatch(reviews=merged),
        )

    review_rows, gold_rows = _review_rows_and_gold(trials, final_batches)
    decision_counts = Counter(item["decision"] for item in review_rows)
    reason_counts = Counter(item["reason_code"] for item in review_rows)
    selected_count = sum(
        len(item["criterion_candidates"]) for item in selected_trials
    )
    remaining_uncertain = sum(
        item["decision"] == "uncertain" for item in review_rows
    )
    source_digest = portable_text_sha256(source_path)
    base_usage = dict(base_document.get("usage", {}))
    max_usage = _sum_usage(max_usage_rows)
    review_document = {
        "status": "preliminary_tiered_ai_review",
        "authority": (
            "AI-generated research draft; not physician gold and not independent "
            "two-person consensus"
        ),
        "source_sha256": source_digest,
        "source_section": resolved_section,
        "group_ids": selected_groups,
        "base_review_sha256": portable_text_sha256(base_review_path),
        "base_review_status": base_document.get("status"),
        "base_model": (
            base_document.get("resolution_model")
            or base_document.get("model")
            or base_document.get("base_model")
        ),
        "base_effort": (
            base_document.get("resolution_effort")
            or base_document.get("effort")
            or base_document.get("base_effort")
        ),
        "resolution_model": model_id,
        "resolution_effort": effort,
        "selection_mode": selection_mode,
        "source_line_count": len(review_rows),
        "maximum_review_line_count": selected_count,
        "changed_by_max_count": len(changed_by_max),
        "changed_by_max_candidate_ids": changed_by_max,
        "remaining_uncertain_count": remaining_uncertain,
        "decision_counts": dict(decision_counts),
        "reason_counts": dict(reason_counts),
        "usage": {"base": base_usage, "maximum_resolution": max_usage},
        "reviews": review_rows,
    }
    gold_document = {
        "status": "preliminary_tiered_ai_reviewed_gold",
        "authority": review_document["authority"],
        "source_sha256": source_digest,
        "criterion_count": len(gold_rows),
        "criteria": gold_rows,
    }
    _write_json(review_output, review_document)
    _write_json(gold_output, gold_document)
    return {
        "review_output": str(review_output),
        "gold_output": str(gold_output),
        "source_line_count": len(review_rows),
        "maximum_review_line_count": selected_count,
        "criterion_count": len(gold_rows),
        "changed_by_max_count": len(changed_by_max),
        "remaining_uncertain_count": remaining_uncertain,
        "decision_counts": dict(decision_counts),
        "usage": max_usage,
    }


def build_conservative_natural_ai_gold(
    *,
    source_path: str | Path,
    tiered_review_path: str | Path,
    selection_config_path: str | Path,
    output_path: str | Path,
    source_section: NaturalEvaluationSourceSection | None = None,
    group_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Keep only high-confidence, source-validated AI annotations.

    The result is a research draft for later human checking.  It deliberately
    excludes medium-confidence annotations and reports trials that no longer
    meet the source-selection minimum after this stricter filter.
    """

    source_path = Path(source_path)
    tiered_review_path = Path(tiered_review_path)
    output_path = Path(output_path)
    if output_path.exists():
        raise FileExistsError("conservative AI gold output already exists")

    source = json.loads(source_path.read_text(encoding="utf-8"))
    review_document = json.loads(tiered_review_path.read_text(encoding="utf-8"))
    review_section = str(review_document.get("source_section", "trials"))
    resolved_section = source_section or review_section
    if resolved_section != review_section:
        raise ValueError("conservative gold source section differs from the review")
    review_groups = review_document.get("group_ids")
    resolved_groups = list(group_ids) if group_ids is not None else review_groups
    if group_ids is not None and review_groups is not None:
        if list(group_ids) != list(review_groups):
            raise ValueError("conservative gold disease groups differ from the review")
    trials, selected_groups = _selected_source_trials(
        source,
        source_section=resolved_section,
        group_ids=resolved_groups,
    )
    source_digest = portable_text_sha256(source_path)
    if review_document.get("source_sha256") != source_digest:
        raise ValueError("tiered AI review does not match the frozen source")

    source_candidates = {
        str(candidate["candidate_id"]): (trial, candidate)
        for trial in trials
        for candidate in trial["criterion_candidates"]
    }
    raw_reviews = review_document.get("reviews")
    if not isinstance(raw_reviews, list):
        raise ValueError("tiered AI review has no review rows")
    raw_by_id: dict[str, Mapping[str, Any]] = {}
    for raw in raw_reviews:
        candidate_id = str(raw.get("candidate_id", ""))
        if candidate_id in raw_by_id:
            raise ValueError(f"tiered AI review repeated {candidate_id}")
        if candidate_id not in source_candidates:
            raise ValueError(f"tiered AI review contains unknown {candidate_id}")
        trial, candidate = source_candidates[candidate_id]
        exact_fields = {
            "group_id": trial["group_id"],
            "nct_id": trial["nct_id"],
            "section": candidate["section_hint"],
            "line_number": candidate["line_number"],
            "start_char": candidate["start_char"],
            "end_char": candidate["end_char"],
            "source_text": candidate["source_text"],
        }
        if any(raw.get(key) != value for key, value in exact_fields.items()):
            raise ValueError(f"tiered AI review source fields changed for {candidate_id}")
        raw_by_id[candidate_id] = raw
    if set(raw_by_id) != set(source_candidates):
        missing = sorted(set(source_candidates) - set(raw_by_id))
        raise ValueError(f"tiered AI review is missing source rows: {missing[:3]}")

    batches: dict[str, AiCriterionReviewBatch] = {}
    for trial in trials:
        reviews = []
        for candidate in trial["criterion_candidates"]:
            candidate_id = str(candidate["candidate_id"])
            raw = raw_by_id[candidate_id]
            reviews.append(
                AiCriterionLineReview.model_validate(
                    {
                        "candidate_id": candidate_id,
                        "decision": raw["decision"],
                        "confidence": raw["confidence"],
                        "reason_code": raw["reason_code"],
                        "annotations": raw["annotations"],
                        "note": raw.get("note"),
                    }
                )
            )
        batches[str(trial["nct_id"])] = validate_ai_review_batch(
            trial,
            AiCriterionReviewBatch(reviews=reviews),
        )

    review_rows, gold_rows = _review_rows_and_gold(trials, batches)
    high_confidence_rows = [
        row for row in gold_rows if row["confidence"] == "high"
    ]
    high_by_candidate: dict[str, list[dict[str, Any]]] = {}
    for row in high_confidence_rows:
        high_by_candidate.setdefault(str(row["candidate_id"]), []).append(row)
    representation_ready_ids = set()
    for candidate_id, rows in high_by_candidate.items():
        if len(rows) == 1:
            representation_ready_ids.add(candidate_id)
            continue
        numeric_rows = [row for row in rows if row["operator"] is not None]
        numeric_range = (
            len(numeric_rows) == len(rows)
            and len(
                {
                    (row["fact_code"], row["unit"])
                    for row in numeric_rows
                }
            )
            == 1
            and {row["operator"] for row in numeric_rows}
            <= {"gt", "gte", "lt", "lte"}
            and any(row["operator"] in {"gt", "gte"} for row in numeric_rows)
            and any(row["operator"] in {"lt", "lte"} for row in numeric_rows)
        )
        if numeric_range:
            representation_ready_ids.add(candidate_id)
    conservative_rows = [
        row
        for row in high_confidence_rows
        if row["candidate_id"] in representation_ready_ids
    ]
    deferred_candidate_ids = sorted(
        set(high_by_candidate) - representation_ready_ids
    )
    duplicate_keys = []
    seen_keys = set()
    for row in conservative_rows:
        key = (
            row["candidate_id"],
            row["fact_code"],
            row.get("expected_value"),
            row.get("operator"),
            row.get("threshold"),
            row.get("unit"),
        )
        if key in seen_keys:
            duplicate_keys.append(key)
        seen_keys.add(key)
    if duplicate_keys:
        raise ValueError("conservative AI gold contains duplicate annotations")

    config = load_natural_evaluation_selection_config(selection_config_path)
    included_source_ids = set(representation_ready_ids)
    trial_coverage = []
    for trial in trials:
        candidate_ids = {
            str(item["candidate_id"]) for item in trial["criterion_candidates"]
        }
        accepted_ids = candidate_ids & included_source_ids
        criteria_count = sum(
            row["nct_id"] == trial["nct_id"] for row in conservative_rows
        )
        trial_coverage.append(
            {
                "group_id": trial["group_id"],
                "nct_id": trial["nct_id"],
                "source_line_count": len(candidate_ids),
                "accepted_source_line_count": len(accepted_ids),
                "criterion_count": criteria_count,
                "meets_minimum": (
                    len(accepted_ids) >= config.minimum_objective_lines
                ),
            }
        )
    low_coverage_ids = [
        row["nct_id"] for row in trial_coverage if not row["meets_minimum"]
    ]
    kind_counts = Counter(row["kind"] for row in conservative_rows)
    group_counts = Counter(row["group_id"] for row in conservative_rows)
    rule_type_counts = Counter(
        "numeric" if row["operator"] is not None else "categorical"
        for row in conservative_rows
    )
    output = {
        "status": "preliminary_conservative_ai_gold",
        "authority": (
            "High-confidence AI research draft; not physician gold and not "
            "independent two-person consensus"
        ),
        "source_sha256": source_digest,
        "source_section": resolved_section,
        "group_ids": selected_groups,
        "tiered_review_sha256": portable_text_sha256(tiered_review_path),
        "selection_config": str(selection_config_path),
        "minimum_accepted_source_lines_per_trial": (
            config.minimum_objective_lines
        ),
        "representation_rule": (
            "One independently testable annotation per source line, or one "
            "numeric lower-and-upper range on the same fact and unit"
        ),
        "source_line_count": len(review_rows),
        "high_confidence_source_line_count": len(high_by_candidate),
        "high_confidence_annotation_count": len(high_confidence_rows),
        "accepted_source_line_count": len(included_source_ids),
        "criterion_count": len(conservative_rows),
        "deferred_complex_source_line_count": len(deferred_candidate_ids),
        "deferred_complex_candidate_ids": deferred_candidate_ids,
        "kind_counts": dict(kind_counts),
        "group_counts": dict(group_counts),
        "rule_type_counts": dict(rule_type_counts),
        "low_coverage_trial_ids": low_coverage_ids,
        "trial_coverage": trial_coverage,
        "criteria": conservative_rows,
    }
    _write_json(output_path, output)
    return {
        "output": str(output_path),
        "source_line_count": len(review_rows),
        "high_confidence_source_line_count": len(high_by_candidate),
        "high_confidence_annotation_count": len(high_confidence_rows),
        "accepted_source_line_count": len(included_source_ids),
        "criterion_count": len(conservative_rows),
        "deferred_complex_source_line_count": len(deferred_candidate_ids),
        "low_coverage_trial_ids": low_coverage_ids,
        "rule_type_counts": dict(rule_type_counts),
    }


__all__ = [
    "AUDIT_PASS_PROMPT",
    "ALLOWED_EXPECTED_VALUES",
    "FIRST_PASS_PROMPT",
    "AiCriterionLineReview",
    "AiCriterionReviewBatch",
    "AiObjectiveAnnotation",
    "build_conservative_natural_ai_gold",
    "run_natural_evaluation_ai_review",
    "run_natural_evaluation_max_resolution",
    "validate_ai_review_batch",
]
