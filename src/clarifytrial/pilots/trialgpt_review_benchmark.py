"""Strong single judgment with exact-output no-web and web review arms."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from enum import StrEnum
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..datasets.trialgpt import CriterionType, TrialGPTPair
from ..llm.base import ModelCall, StructuredModel
from .trialgpt_architecture import (
    ArchitectureCallRecord,
    ArchitectureCriterionJudgment,
    ArchitectureMatcherResponse,
    ArchitectureReviewerResponse,
    MAX_CRITERIA_PER_JUDGMENT_CALL,
    TrialFinalStatus,
    TrialGPTArchitectureCase,
    _validate_judgments,
    aggregate_trial_status,
    build_architecture_case,
    validate_m2_review_transition,
)


PROTOCOL_ID = "trialgpt-strong-review-v2"
SINGLE_PROMPT_ID = "prompts/trialgpt_strong_single_v1.md"
NO_WEB_REVIEW_PROMPT_ID = "prompts/trialgpt_strong_reviewer_no_web_v2.md"
WEB_REVIEW_PROMPT_ID = "prompts/trialgpt_strong_reviewer_web_v2.md"

_REVIEWABLE_FLAGS = {
    "strong_implicit_evidence_candidate",
    "expected_documentation_absence_candidate",
    "evidence_conflict",
}


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ReviewArm(StrEnum):
    SINGLE = "S1-R"
    NO_WEB = "S1-RV"
    WEB = "S1-RW"


class StrongReviewCaseResult(_StrictModel):
    protocol_id: str = PROTOCOL_ID
    case_id: str
    pair_id: str
    baseline_predictions: tuple[ArchitectureCriterionJudgment, ...]
    no_web_predictions: tuple[ArchitectureCriterionJudgment, ...]
    web_predictions: tuple[ArchitectureCriterionJudgment, ...]
    review_selected_ids: tuple[int, ...]
    no_web_changed: int = Field(ge=0)
    web_changed: int = Field(ge=0)
    calls: tuple[ArchitectureCallRecord, ...]


class ReviewArmMetrics(_StrictModel):
    arm: ReviewArm
    cases: int = Field(ge=0)
    criteria: int = Field(ge=0)
    criterion_accuracy: float = Field(ge=0, le=1)
    trial_status_accuracy: float = Field(ge=0, le=1)
    expert_nei_preservation: float = Field(ge=0, le=1)
    expert_decisive_to_nei: float = Field(ge=0, le=1)
    public_label_agreement: float = Field(ge=0, le=1)
    review_selected: int = Field(ge=0)
    review_changed: int = Field(ge=0)
    wrong_to_correct: int = Field(ge=0)
    correct_to_wrong: int = Field(ge=0)
    system_input_tokens: int = Field(ge=0)
    system_output_tokens: int = Field(ge=0)
    system_reasoning_tokens: int = Field(ge=0)
    system_total_tokens: int = Field(ge=0)
    web_search_actions: int = Field(ge=0)


class StrongReviewBenchmark(_StrictModel):
    protocol_id: str = PROTOCOL_ID
    cases: int = Field(ge=0)
    criteria: int = Field(ge=0)
    arm_metrics: dict[str, ReviewArmMetrics]
    executed_input_tokens: int = Field(ge=0)
    executed_output_tokens: int = Field(ge=0)
    executed_reasoning_tokens: int = Field(ge=0)
    executed_total_tokens: int = Field(ge=0)
    web_search_actions: int = Field(ge=0)


def _case_subset(
    case: TrialGPTArchitectureCase,
    criteria: Sequence[Any],
    *,
    suffix: str,
) -> TrialGPTArchitectureCase:
    wanted = {item.annotation_id for item in criteria}
    snapshot_rows = tuple(
        item
        for item in case.bm25_snapshot.criteria
        if item.annotation_id in wanted
    )
    snapshot = case.bm25_snapshot.model_copy(
        update={
            "snapshot_id": f"{case.bm25_snapshot.snapshot_id}-{suffix}",
            "criteria": snapshot_rows,
        }
    )
    return case.model_copy(update={"criteria": tuple(criteria), "bm25_snapshot": snapshot})


def _criterion_type_batches(
    case: TrialGPTArchitectureCase,
) -> tuple[TrialGPTArchitectureCase, ...]:
    batches: list[TrialGPTArchitectureCase] = []
    for criterion_type in ("inclusion", "exclusion"):
        criteria = [
            item for item in case.criteria if item.criterion_type == criterion_type
        ]
        for start in range(0, len(criteria), MAX_CRITERIA_PER_JUDGMENT_CALL):
            chunk = criteria[start : start + MAX_CRITERIA_PER_JUDGMENT_CALL]
            if chunk:
                batches.append(
                    _case_subset(
                        case,
                        chunk,
                        suffix=f"{criterion_type}-{start // MAX_CRITERIA_PER_JUDGMENT_CALL + 1}",
                    )
                )
    return tuple(batches)


def _review_payload(
    case: TrialGPTArchitectureCase,
    initial: Mapping[int, ArchitectureCriterionJudgment],
    review_ids: Sequence[int],
) -> dict[str, Any]:
    criteria = {item.annotation_id: item for item in case.criteria}
    retrieval = {item.annotation_id: item for item in case.bm25_snapshot.criteria}
    return {
        "case_id": case.case_id,
        "raw_patient_note": case.raw_patient_note,
        "trial": case.trial.model_dump(mode="json"),
        "criteria": [criteria[item].model_dump(mode="json") for item in review_ids],
        "bm25_snapshot": [
            retrieval[item].model_dump(mode="json") for item in review_ids
        ],
        "missingness_policy_id": case.missingness_policy_id,
        "missingness_rules": list(case.missingness_rules),
        "label_semantics_id": case.label_semantics_id,
        "initial_judgments": [
            initial[item].model_dump(mode="json") for item in review_ids
        ],
    }


def _invoke(
    model: StructuredModel,
    *,
    call_id: str,
    role: str,
    prompt_id: str,
    payload: Mapping[str, Any],
    response_model: type[BaseModel],
    calls: list[ArchitectureCallRecord],
) -> BaseModel:
    response, usage = model.complete(
        ModelCall(
            role=role,
            prompt_id=prompt_id,
            payload=payload,
            response_model=response_model,
        )
    )
    typed = (
        response
        if isinstance(response, response_model)
        else response_model.model_validate(
            response.model_dump(mode="json")
            if isinstance(response, BaseModel)
            else response
        )
    )
    calls.append(
        ArchitectureCallRecord(
            call_id=call_id,
            role=role,
            prompt_id=prompt_id,
            status="completed",
            usage=asdict(usage),
        )
    )
    return typed


def _normalize_words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.casefold())


def validate_web_search_events(
    case: TrialGPTArchitectureCase,
    calls: Sequence[ArchitectureCallRecord],
) -> None:
    """Reject benchmark-answer searches while retaining general medical research."""

    events: list[Mapping[str, Any]] = []
    for call in calls:
        if call.role != "strong_reviewer_web" or call.usage is None:
            continue
        raw_events = call.usage.get("web_search_events", ())
        if isinstance(raw_events, (list, tuple)):
            events.extend(item for item in raw_events if isinstance(item, Mapping))
    search_queries: list[str] = []
    for event in events:
        action = event.get("action")
        action_type = action.get("type") if isinstance(action, Mapping) else None
        if action_type == "search":
            query = event.get("query")
            if isinstance(query, str) and query.strip():
                search_queries.append(query)
    web_call_count = sum(call.role == "strong_reviewer_web" for call in calls)
    if web_call_count and len(search_queries) < web_call_count:
        raise ValueError("web reviewer did not perform the required general medical search")
    if len(search_queries) > 3 * max(1, web_call_count):
        raise ValueError("web reviewer exceeded the three-query limit per call")

    forbidden = {
        "trialgpt",
        "expert eligibility",
        "annotation id",
        " ".join(_normalize_words(case.patient_id)),
        " ".join(_normalize_words(case.trial_id)),
    }
    note_words = _normalize_words(case.raw_patient_note)
    note_ngrams = {
        " ".join(note_words[index : index + 8])
        for index in range(max(0, len(note_words) - 7))
    }
    criterion_phrases = {
        " ".join(_normalize_words(item.criterion_text))
        for item in case.criteria
        if len(_normalize_words(item.criterion_text)) >= 6
    }
    for query in search_queries:
        normalized = " ".join(_normalize_words(query))
        if any(marker and marker in normalized for marker in forbidden):
            raise ValueError("web reviewer searched a forbidden benchmark identifier")
        if any(phrase in normalized for phrase in note_ngrams):
            raise ValueError("web reviewer searched patient-note text")
        if any(phrase in normalized for phrase in criterion_phrases):
            raise ValueError("web reviewer searched a criterion verbatim")


def _review(
    case: TrialGPTArchitectureCase,
    baseline: Sequence[ArchitectureCriterionJudgment],
    model: StructuredModel,
    *,
    arm: ReviewArm,
    calls: list[ArchitectureCallRecord],
) -> tuple[tuple[ArchitectureCriterionJudgment, ...], int]:
    initial = {item.annotation_id: item for item in baseline}
    selected = sorted(
        item.annotation_id
        for item in baseline
        if item.eligibility_label == "not enough information"
        and _REVIEWABLE_FLAGS.intersection(item.review_flags)
    )
    if not selected:
        return tuple(baseline), 0
    criteria = {item.annotation_id: item for item in case.criteria}
    prompt_id = (
        NO_WEB_REVIEW_PROMPT_ID if arm is ReviewArm.NO_WEB else WEB_REVIEW_PROMPT_ID
    )
    role = "strong_reviewer_no_web" if arm is ReviewArm.NO_WEB else "strong_reviewer_web"
    arm_calls_start = len(calls)
    for batch_number, start in enumerate(
        range(0, len(selected), MAX_CRITERIA_PER_JUDGMENT_CALL), start=1
    ):
        review_ids = selected[start : start + MAX_CRITERIA_PER_JUDGMENT_CALL]
        response = ArchitectureReviewerResponse.model_validate(
            _invoke(
                model,
                call_id=f"{case.case_id}:{arm.value}:review-{batch_number}",
                role=role,
                prompt_id=prompt_id,
                payload=_review_payload(case, initial, review_ids),
                response_model=ArchitectureReviewerResponse,
                calls=calls,
            )
        )
        _validate_judgments(response.reviews, case, expected_ids=review_ids)
        for reviewed in response.reviews:
            validate_m2_review_transition(
                initial[reviewed.annotation_id],
                reviewed,
                criteria[reviewed.annotation_id],
            )
            initial[reviewed.annotation_id] = reviewed
    if arm is ReviewArm.WEB:
        validate_web_search_events(case, calls[arm_calls_start:])
    predictions = tuple(initial[item.annotation_id] for item in case.criteria)
    changed = sum(
        after.eligibility_label != before.eligibility_label
        for before, after in zip(baseline, predictions, strict=True)
    )
    return predictions, changed


def run_strong_review_case(
    pair: TrialGPTPair,
    single_and_no_web_model: StructuredModel,
    web_model: StructuredModel,
    *,
    retrieval_top_k: int = 5,
) -> StrongReviewCaseResult:
    """Run one strong baseline once, then reuse its exact output in both reviews."""

    case = build_architecture_case(pair, retrieval_top_k=retrieval_top_k)
    calls: list[ArchitectureCallRecord] = []
    baseline_by_id: dict[int, ArchitectureCriterionJudgment] = {}
    for batch_number, batch in enumerate(_criterion_type_batches(case), start=1):
        response = ArchitectureMatcherResponse.model_validate(
            _invoke(
                single_and_no_web_model,
                call_id=f"{case.case_id}:S1-R:judge-{batch_number}",
                role="strong_single_judge",
                prompt_id=SINGLE_PROMPT_ID,
                payload={"shared_input": batch.model_dump(mode="json")},
                response_model=ArchitectureMatcherResponse,
                calls=calls,
            )
        )
        _validate_judgments(response.judgments, batch)
        baseline_by_id.update(
            (item.annotation_id, item) for item in response.judgments
        )
    baseline = tuple(baseline_by_id[item.annotation_id] for item in case.criteria)
    selected = tuple(
        item.annotation_id
        for item in baseline
        if item.eligibility_label == "not enough information"
        and _REVIEWABLE_FLAGS.intersection(item.review_flags)
    )
    no_web, no_web_changed = _review(
        case,
        baseline,
        single_and_no_web_model,
        arm=ReviewArm.NO_WEB,
        calls=calls,
    )
    web, web_changed = _review(
        case,
        baseline,
        web_model,
        arm=ReviewArm.WEB,
        calls=calls,
    )
    return StrongReviewCaseResult(
        case_id=case.case_id,
        pair_id=case.pair_id,
        baseline_predictions=baseline,
        no_web_predictions=no_web,
        web_predictions=web,
        review_selected_ids=selected,
        no_web_changed=no_web_changed,
        web_changed=web_changed,
        calls=tuple(calls),
    )


def _usage_totals(calls: Sequence[ArchitectureCallRecord]) -> dict[str, int]:
    totals = {"input": 0, "output": 0, "reasoning": 0, "total": 0, "web": 0}
    for call in calls:
        usage = call.usage or {}
        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        reasoning = int(usage.get("thinking_tokens") or 0)
        total = int(usage.get("total_tokens") or input_tokens + output_tokens)
        totals["input"] += input_tokens
        totals["output"] += output_tokens
        totals["reasoning"] += reasoning
        totals["total"] += total
        events = usage.get("web_search_events") or ()
        if isinstance(events, (list, tuple)):
            totals["web"] += sum(
                isinstance(event, Mapping)
                and isinstance(event.get("action"), Mapping)
                and event["action"].get("type") == "search"
                for event in events
            )
    return totals


def _status(
    pair: TrialGPTPair,
    predictions: Sequence[ArchitectureCriterionJudgment],
) -> TrialFinalStatus:
    by_id = {item.annotation_id: item for item in predictions}
    return aggregate_trial_status(
        [
            (row.criterion_type, by_id[row.annotation_id].eligibility_label)
            for row in pair.criteria
        ]
    )


def assemble_strong_review_benchmark(
    pairs: Sequence[TrialGPTPair],
    results: Sequence[StrongReviewCaseResult],
) -> StrongReviewBenchmark:
    pair_by_id = {f"{item.patient_id}/{item.trial_id}": item for item in pairs}
    if len(pair_by_id) != len(pairs) or {item.pair_id for item in results} != set(pair_by_id):
        raise ValueError("review results must cover every pair exactly once")
    if len({item.pair_id for item in results}) != len(results):
        raise ValueError("review results repeat a pair")

    prediction_fields = {
        ReviewArm.SINGLE: "baseline_predictions",
        ReviewArm.NO_WEB: "no_web_predictions",
        ReviewArm.WEB: "web_predictions",
    }
    metrics: dict[str, ReviewArmMetrics] = {}
    for arm, field in prediction_fields.items():
        correct = public = trials = expert_nei = kept_nei = 0
        decisive = decisive_to_nei = wrong_to_correct = correct_to_wrong = 0
        selected = changed = 0
        arm_calls: list[ArchitectureCallRecord] = []
        criteria_total = 0
        for result in results:
            pair = pair_by_id[result.pair_id]
            predictions = getattr(result, field)
            baseline = {
                item.annotation_id: item for item in result.baseline_predictions
            }
            by_id = {item.annotation_id: item for item in predictions}
            criteria_total += len(pair.criteria)
            expert_status = aggregate_trial_status(
                [(row.criterion_type, row.expert_eligibility) for row in pair.criteria]
            )
            trials += _status(pair, predictions) is expert_status
            for row in pair.criteria:
                predicted = by_id[row.annotation_id].eligibility_label
                before = baseline[row.annotation_id].eligibility_label
                after_correct = predicted == row.expert_eligibility
                before_correct = before == row.expert_eligibility
                correct += after_correct
                public += predicted == row.gpt4_eligibility
                if row.expert_eligibility == "not enough information":
                    expert_nei += 1
                    kept_nei += predicted == "not enough information"
                else:
                    decisive += 1
                    decisive_to_nei += predicted == "not enough information"
                if arm is not ReviewArm.SINGLE:
                    wrong_to_correct += (not before_correct) and after_correct
                    correct_to_wrong += before_correct and (not after_correct)
            if arm is not ReviewArm.SINGLE:
                selected += len(result.review_selected_ids)
                changed += (
                    result.no_web_changed
                    if arm is ReviewArm.NO_WEB
                    else result.web_changed
                )
            roles = {
                ReviewArm.SINGLE: {"strong_single_judge"},
                ReviewArm.NO_WEB: {"strong_single_judge", "strong_reviewer_no_web"},
                ReviewArm.WEB: {"strong_single_judge", "strong_reviewer_web"},
            }[arm]
            arm_calls.extend(call for call in result.calls if call.role in roles)
        usage = _usage_totals(arm_calls)
        metrics[arm.value] = ReviewArmMetrics(
            arm=arm,
            cases=len(results),
            criteria=criteria_total,
            criterion_accuracy=0 if not criteria_total else correct / criteria_total,
            trial_status_accuracy=0 if not results else trials / len(results),
            expert_nei_preservation=0 if not expert_nei else kept_nei / expert_nei,
            expert_decisive_to_nei=0 if not decisive else decisive_to_nei / decisive,
            public_label_agreement=0 if not criteria_total else public / criteria_total,
            review_selected=selected,
            review_changed=changed,
            wrong_to_correct=wrong_to_correct,
            correct_to_wrong=correct_to_wrong,
            system_input_tokens=usage["input"],
            system_output_tokens=usage["output"],
            system_reasoning_tokens=usage["reasoning"],
            system_total_tokens=usage["total"],
            web_search_actions=usage["web"],
        )
    executed = _usage_totals([call for result in results for call in result.calls])
    return StrongReviewBenchmark(
        cases=len(results),
        criteria=sum(len(item.criteria) for item in pairs),
        arm_metrics=metrics,
        executed_input_tokens=executed["input"],
        executed_output_tokens=executed["output"],
        executed_reasoning_tokens=executed["reasoning"],
        executed_total_tokens=executed["total"],
        web_search_actions=executed["web"],
    )


__all__ = [
    "NO_WEB_REVIEW_PROMPT_ID",
    "PROTOCOL_ID",
    "ReviewArm",
    "SINGLE_PROMPT_ID",
    "StrongReviewBenchmark",
    "StrongReviewCaseResult",
    "WEB_REVIEW_PROMPT_ID",
    "assemble_strong_review_benchmark",
    "run_strong_review_case",
    "validate_web_search_events",
]
