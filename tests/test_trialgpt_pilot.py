from __future__ import annotations

import json
from pathlib import Path

from clarifytrial.datasets.trialgpt import (
    TrialGPTCriterionRow,
    group_patient_trial_pairs,
)
from clarifytrial.llm.base import ModelCall, ModelUsage
from clarifytrial.pilots.trialgpt_sonnet import (
    TrialGPTPredictionBatch,
    TrialGPTReviewBatch,
    build_trialgpt_payload,
    run_trialgpt_pilot,
)


def _row(
    annotation_id: int,
    criterion_type: str,
    expert_label: str,
) -> TrialGPTCriterionRow:
    return TrialGPTCriterionRow(
        annotation_id=annotation_id,
        patient_id="synthetic-patient",
        note="0. The synthetic patient is 55 years old.\n1. No infection is documented.",
        trial_id="NCT-SYNTHETIC",
        trial_title="Synthetic trial",
        criterion_type=criterion_type,
        criterion_text=(
            "Age 18 years or older"
            if criterion_type == "inclusion"
            else "Active infection"
        ),
        gpt4_explanation="Published baseline explanation",
        explanation_correctness="Correct",
        gpt4_sentences=[annotation_id],
        expert_sentences=[annotation_id],
        gpt4_eligibility=expert_label,
        expert_eligibility=expert_label,
        training=False,
    )


class _CapturingModel:
    def __init__(self) -> None:
        self.calls: list[ModelCall] = []

    def complete(self, call: ModelCall):
        self.calls.append(call)
        allowed = call.payload["allowed_labels"]
        label = "included" if "included" in allowed else "not excluded"
        judgments = [
            {
                "annotation_id": item["annotation_id"],
                "explanation": "The cited synthetic sentence supports this label.",
                "evidence_sentence_ids": [item["annotation_id"]],
                "eligibility_label": label,
            }
            for item in call.payload["criteria"]
        ]
        return (
            TrialGPTPredictionBatch.model_validate({"judgments": judgments}),
            ModelUsage(
                model_id="claude-sonnet-5",
                effort="medium",
                input_tokens=100,
                output_tokens=20,
                thinking_tokens=5,
                latency_ms=10,
                finish_reason="end_turn",
            ),
        )


def test_payload_excludes_all_public_and_expert_answers() -> None:
    pair = group_patient_trial_pairs(
        [_row(0, "inclusion", "included"), _row(1, "exclusion", "not excluded")]
    )[0]

    payload = build_trialgpt_payload(pair, "inclusion")
    serialized = json.dumps(payload)

    assert "gpt4_eligibility" not in serialized
    assert "expert_eligibility" not in serialized
    assert "Published baseline explanation" not in serialized
    assert payload["criteria"] == [
        {"annotation_id": 0, "criterion_text": "Age 18 years or older"}
    ]


def test_pilot_runs_two_bundled_calls_then_scores_and_costs(tmp_path: Path) -> None:
    rows = [
        _row(0, "inclusion", "included"),
        _row(1, "exclusion", "not excluded"),
    ]
    model = _CapturingModel()

    summary = run_trialgpt_pilot(
        group_patient_trial_pairs(rows),
        model,
        tmp_path,
    )

    assert len(model.calls) == 2
    assert summary.expected_calls == 2
    assert summary.sample_pair_ids == ["synthetic-patient/NCT-SYNTHETIC"]
    assert summary.completed_calls == 2
    assert summary.failed_calls == 0
    assert summary.sonnet_vs_expert.label_accuracy == 1.0
    assert summary.public_trialgpt_vs_expert.label_accuracy == 1.0
    assert summary.usage.input_tokens == 200
    assert summary.usage.output_tokens == 40
    assert summary.usage.total_cost_usd == 0.0008
    assert summary.thinking_tokens == 10
    assert (tmp_path / "summary.json").is_file()
    assert len((tmp_path / "predictions.jsonl").read_text().splitlines()) == 2


def test_model_failure_is_not_replaced_with_gold(tmp_path: Path) -> None:
    class _FailingModel:
        def complete(self, call):
            raise RuntimeError("safe synthetic failure")

    rows = [_row(0, "inclusion", "included")]
    summary = run_trialgpt_pilot(
        group_patient_trial_pairs(rows),
        _FailingModel(),
        tmp_path,
    )

    assert summary.completed_calls == 0
    assert summary.failed_calls == 1
    assert summary.sonnet_vs_expert.completed == 0
    assert summary.sonnet_vs_expert.label_accuracy == 0.0
    prediction = json.loads((tmp_path / "predictions.jsonl").read_text())
    assert prediction["sonnet"] is None


def test_nei_only_review_changes_final_without_exposing_gold(tmp_path: Path) -> None:
    class _ReviewingModel:
        def __init__(self) -> None:
            self.calls: list[ModelCall] = []

        def complete(self, call: ModelCall):
            self.calls.append(call)
            if call.role == "trialgpt_criterion_judge":
                response = TrialGPTPredictionBatch.model_validate(
                    {
                        "judgments": [
                            {
                                "annotation_id": 1,
                                "explanation": "The note does not mention infection.",
                                "evidence_sentence_ids": [],
                                "eligibility_label": "not enough information",
                            }
                        ]
                    }
                )
            else:
                serialized = json.dumps(call.payload)
                assert "expert_eligibility" not in serialized
                assert "gpt4_eligibility" not in serialized
                assert call.payload["criteria"][0]["initial_judgment"][
                    "eligibility_label"
                ] == "not enough information"
                response = TrialGPTReviewBatch.model_validate(
                    {
                        "reviews": [
                            {
                                "annotation_id": 1,
                                "review_reason": "expected_documentation_absence",
                                "explanation": (
                                    "Expected documentation absence supports no active "
                                    "infection."
                                ),
                                "evidence_sentence_ids": [],
                                "eligibility_label": "not excluded",
                            }
                        ]
                    }
                )
            return response, ModelUsage(
                model_id="claude-sonnet-5",
                effort="medium",
                input_tokens=100,
                output_tokens=20,
                thinking_tokens=0,
                latency_ms=10,
                finish_reason="end_turn",
            )

    model = _ReviewingModel()
    summary = run_trialgpt_pilot(
        group_patient_trial_pairs([_row(1, "exclusion", "not excluded")]),
        model,
        tmp_path,
        variant="calibrated-review",
        prompt_id="prompts/trialgpt_criterion_judge_calibrated.md",
        review_prompt_id="prompts/trialgpt_criterion_reviewer.md",
    )

    assert [call.role for call in model.calls] == [
        "trialgpt_criterion_judge",
        "trialgpt_criterion_reviewer",
    ]
    assert summary.initial_vs_expert is not None
    assert summary.initial_vs_expert.label_accuracy == 0.0
    assert summary.sonnet_vs_expert.label_accuracy == 1.0
    assert summary.initial_calls == 1
    assert summary.review_calls == 1
    assert summary.expected_calls == 2
    record = json.loads((tmp_path / "predictions.jsonl").read_text())
    assert record["initial_sonnet"]["eligibility_label"] == (
        "not enough information"
    )
    assert record["review"]["review_reason"] == (
        "expected_documentation_absence"
    )
    assert record["sonnet"]["eligibility_label"] == "not excluded"


def test_out_of_range_evidence_sentence_is_recorded_as_failure(tmp_path: Path) -> None:
    class _BadEvidenceModel:
        def complete(self, call: ModelCall):
            return (
                TrialGPTPredictionBatch.model_validate(
                    {
                        "judgments": [
                            {
                                "annotation_id": 0,
                                "explanation": "Synthetic invalid citation.",
                                "evidence_sentence_ids": [99],
                                "eligibility_label": "included",
                            }
                        ]
                    }
                ),
                ModelUsage(
                    model_id="claude-sonnet-5",
                    effort="medium",
                    input_tokens=10,
                    output_tokens=5,
                    finish_reason="end_turn",
                ),
            )

    summary = run_trialgpt_pilot(
        group_patient_trial_pairs([_row(0, "inclusion", "included")]),
        _BadEvidenceModel(),
        tmp_path,
    )

    assert summary.completed_calls == 0
    assert summary.failed_calls == 1
    failure = json.loads((tmp_path / "failures.json").read_text())["failures"][0]
    assert failure["stage"] == "initial"
    assert "sentence ID absent" in failure["error"]
