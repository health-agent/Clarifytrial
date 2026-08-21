from __future__ import annotations

from dataclasses import asdict

import pytest

from clarifytrial.datasets.trialgpt import (
    TrialGPTCriterionRow,
    group_patient_trial_pairs,
)
from clarifytrial.llm.base import ModelCall, ModelUsage
from clarifytrial.llm.codex_subscription import CodexModelUsage
from clarifytrial.pilots.trialgpt_architecture import (
    ArchitectureMatcherResponse,
    ArchitectureReviewerResponse,
)
from clarifytrial.pilots.trialgpt_review_benchmark import (
    assemble_strong_review_benchmark,
    run_strong_review_case,
    validate_web_search_events,
)


def _row(annotation_id: int, criterion_type: str, expert: str) -> TrialGPTCriterionRow:
    return TrialGPTCriterionRow(
        annotation_id=annotation_id,
        patient_id="sigir-synthetic",
        note="0. The patient is 55 years old.\n1. No chest tube is documented.",
        trial_id="NCT-SYNTHETIC",
        trial_title="Synthetic trial",
        criterion_type=criterion_type,
        criterion_text=(
            "Age 18 years or older" if criterion_type == "inclusion" else "Chest tube"
        ),
        gpt4_explanation="hidden",
        explanation_correctness="Correct",
        gpt4_sentences=[0] if criterion_type == "inclusion" else [],
        expert_sentences=[0] if criterion_type == "inclusion" else [],
        gpt4_eligibility=expert,
        expert_eligibility=expert,
        training=False,
    )


def _pair():
    return group_patient_trial_pairs(
        [_row(0, "inclusion", "included"), _row(1, "exclusion", "not excluded")]
    )[0]


def _judgment(annotation_id: int, label: str, basis: str, evidence=(), flags=()):
    return {
        "annotation_id": annotation_id,
        "explanation": "Synthetic evidence judgment.",
        "evidence_sentence_ids": list(evidence),
        "eligibility_label": label,
        "evidence_basis": basis,
        "review_flags": list(flags),
    }


class _ReviewModel:
    def __init__(self, *, web: bool = False) -> None:
        self.web = web
        self.calls: list[ModelCall] = []

    def complete(self, call: ModelCall):
        self.calls.append(call)
        if call.role == "strong_single_judge":
            criterion = call.payload["shared_input"]["criteria"][0]
            if criterion["criterion_type"] == "inclusion":
                judgment = _judgment(0, "included", "direct_evidence", (0,))
            else:
                judgment = _judgment(
                    1,
                    "not enough information",
                    "unresolved_information",
                    flags=("expected_documentation_absence_candidate",),
                )
            response = ArchitectureMatcherResponse.model_validate(
                {"judgments": [judgment]}
            )
        else:
            initial = call.payload["initial_judgments"][0]
            assert initial["eligibility_label"] == "not enough information"
            if self.web:
                judgment = _judgment(
                    1, "not excluded", "expected_documentation_absence"
                )
            else:
                judgment = _judgment(
                    1, "not enough information", "unresolved_information"
                )
            response = ArchitectureReviewerResponse.model_validate(
                {"reviews": [judgment]}
            )
        usage = ModelUsage(
            model_id="fake",
            effort="medium",
            input_tokens=10,
            output_tokens=5,
            thinking_tokens=2,
            latency_ms=1,
            finish_reason="stop",
        )
        if self.web and call.role == "strong_reviewer_web":
            usage = CodexModelUsage(
                **asdict(usage),
                web_search_events=(
                    {
                        "query": "chest tube routine clinical documentation",
                        "action": {"type": "search"},
                        "results": [],
                    },
                ),
            )
        return response, usage


def test_exact_single_output_is_reused_by_both_reviewers() -> None:
    no_web = _ReviewModel()
    web = _ReviewModel(web=True)

    result = run_strong_review_case(_pair(), no_web, web, retrieval_top_k=2)

    assert [call.role for call in no_web.calls] == [
        "strong_single_judge",
        "strong_single_judge",
        "strong_reviewer_no_web",
    ]
    assert [call.role for call in web.calls] == ["strong_reviewer_web"]
    assert result.review_selected_ids == (1,)
    assert result.baseline_predictions[1].eligibility_label == "not enough information"
    assert result.no_web_predictions[1].eligibility_label == "not enough information"
    assert result.web_predictions[1].eligibility_label == "not excluded"
    assert no_web.calls[-1].payload["initial_judgments"] == web.calls[-1].payload[
        "initial_judgments"
    ]
    assert {
        call.payload["shared_input"]["criteria"][0]["criterion_type"]
        for call in no_web.calls[:2]
    } == {"inclusion", "exclusion"}


def test_aggregate_reports_review_gain_and_system_token_cost() -> None:
    result = run_strong_review_case(
        _pair(), _ReviewModel(), _ReviewModel(web=True), retrieval_top_k=2
    )

    benchmark = assemble_strong_review_benchmark([_pair()], [result])

    assert benchmark.arm_metrics["S1-R"].criterion_accuracy == 0.5
    assert benchmark.arm_metrics["S1-RV"].criterion_accuracy == 0.5
    assert benchmark.arm_metrics["S1-RW"].criterion_accuracy == 1.0
    assert benchmark.arm_metrics["S1-RW"].wrong_to_correct == 1
    assert benchmark.arm_metrics["S1-RW"].correct_to_wrong == 0
    assert benchmark.arm_metrics["S1-R"].system_total_tokens == 30
    assert benchmark.arm_metrics["S1-RV"].system_total_tokens == 45


def test_web_audit_rejects_patient_identifier_search() -> None:
    result = run_strong_review_case(
        _pair(), _ReviewModel(), _ReviewModel(web=True), retrieval_top_k=2
    )
    unsafe_call = result.calls[-1].model_copy(
        update={
            "usage": {
                **(result.calls[-1].usage or {}),
                "web_search_events": [
                    {
                        "query": "sigir synthetic eligibility answer",
                        "action": {"type": "search"},
                        "results": [],
                    }
                ],
            }
        }
    )
    case_result = result.model_copy(
        update={"calls": (*result.calls[:-1], unsafe_call)}
    )

    from clarifytrial.pilots.trialgpt_architecture import build_architecture_case

    with pytest.raises(ValueError, match="forbidden benchmark identifier"):
        validate_web_search_events(
            build_architecture_case(_pair(), retrieval_top_k=2), case_result.calls
        )
