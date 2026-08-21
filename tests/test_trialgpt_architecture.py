from __future__ import annotations

import json
from collections import Counter

import pytest

from clarifytrial.datasets.trialgpt import TrialGPTCriterionRow, group_patient_trial_pairs
from clarifytrial.llm.base import ModelCall, ModelUsage
from clarifytrial.pilots.trialgpt_architecture import (
    ArchitectureArm,
    ArchitectureArmResult,
    ArchitectureCriterionJudgment,
    ArchitectureMatcherResponse,
    ArchitectureReviewerResponse,
    ArchitectureSingleResponse,
    EvidenceBasis,
    JUDGMENT_BATCHING_ID,
    MAX_CRITERIA_PER_JUDGMENT_CALL,
    RunStatus,
    STATIC_ARCHITECTURE_PROTOCOL_ID,
    STATIC_COORDINATOR_RULE_ID,
    StaticReviewFlag,
    TrialFinalStatus,
    _judgment_batches,
    aggregate_trial_status,
    assemble_trialgpt_architecture_benchmark,
    build_architecture_case,
    pair_id,
    plan_architecture_arm_orders,
    run_trialgpt_architecture_case,
    run_trialgpt_architecture_benchmark,
    select_architecture_pairs,
    select_m2_review_targets,
    validate_m2_review_transition,
)


def _row(
    annotation_id: int,
    criterion_type: str,
    expert_label: str,
    *,
    patient_id: str = "sigir-synthetic",
    trial_id: str = "NCT-SYNTHETIC",
) -> TrialGPTCriterionRow:
    return TrialGPTCriterionRow(
        annotation_id=annotation_id,
        patient_id=patient_id,
        note="0. The synthetic patient is 55 years old.\n1. No active infection is documented.",
        trial_id=trial_id,
        trial_title="Synthetic trial",
        criterion_type=criterion_type,
        criterion_text=(
            "Age 18 years or older"
            if criterion_type == "inclusion"
            else "Active infection"
        ),
        gpt4_explanation="Published answer that must stay hidden",
        explanation_correctness="Correct",
        gpt4_sentences=[0] if criterion_type == "inclusion" else [],
        expert_sentences=[0] if criterion_type == "inclusion" else [],
        gpt4_eligibility=expert_label,
        expert_eligibility=expert_label,
        training=False,
    )


def _pair(patient_id: str = "sigir-synthetic", trial_id: str = "NCT-SYNTHETIC"):
    return group_patient_trial_pairs(
        [
            _row(0, "inclusion", "included", patient_id=patient_id, trial_id=trial_id),
            _row(1, "exclusion", "not excluded", patient_id=patient_id, trial_id=trial_id),
        ]
    )[0]


def _large_pair(criterion_count: int = 41):
    return group_patient_trial_pairs(
        [
            _row(
                annotation_id,
                "inclusion" if annotation_id % 2 == 0 else "exclusion",
                "included" if annotation_id % 2 == 0 else "not excluded",
            )
            for annotation_id in range(criterion_count)
        ]
    )[0]


def _judgment(
    annotation_id: int,
    label: str,
    basis: str,
    *,
    evidence: tuple[int, ...] = (),
    flags: tuple[str, ...] = (),
) -> dict:
    return {
        "annotation_id": annotation_id,
        "explanation": "Synthetic evidence-based judgment.",
        "evidence_sentence_ids": list(evidence),
        "eligibility_label": label,
        "evidence_basis": basis,
        "review_flags": list(flags),
    }


class _ArchitectureModel:
    def __init__(self) -> None:
        self.calls: list[ModelCall] = []

    def complete(self, call: ModelCall):
        self.calls.append(call)
        serialized = json.dumps(call.payload)
        assert "expert_eligibility" not in serialized
        assert "gpt4_eligibility" not in serialized
        assert "Published answer that must stay hidden" not in serialized
        if call.role == "trialgpt_architecture_single":
            response = ArchitectureSingleResponse.model_validate(
                {
                    "judgments": [
                        _judgment(0, "included", "direct_evidence", evidence=(0,)),
                        _judgment(1, "not excluded", "expected_documentation_absence"),
                    ],
                    "final_status": "eligible",
                }
            )
        elif call.role == "matcher_judge":
            response = ArchitectureMatcherResponse.model_validate(
                {
                    "judgments": [
                        _judgment(0, "included", "direct_evidence", evidence=(0,)),
                        _judgment(
                            1,
                            "not enough information",
                            "unresolved_information",
                            flags=("expected_documentation_absence_candidate",),
                        ),
                    ]
                }
            )
        elif call.role == "selective_reviewer":
            response = ArchitectureReviewerResponse.model_validate(
                {
                    "reviews": [
                        _judgment(1, "not excluded", "expected_documentation_absence")
                    ]
                }
            )
        else:
            raise AssertionError(f"unexpected role: {call.role}")
        return response, ModelUsage(
            model_id="scripted-local",
            effort="medium",
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            finish_reason="stop",
        )


class _LargeArchitectureModel(_ArchitectureModel):
    def complete(self, call: ModelCall):
        self.calls.append(call)
        if call.role in {"trialgpt_architecture_single", "matcher_judge"}:
            criteria = call.payload["shared_input"]["criteria"]
        elif call.role == "selective_reviewer":
            criteria = call.payload["criteria"]
        else:
            raise AssertionError(f"unexpected role: {call.role}")
        judgments = []
        for criterion in criteria:
            inclusion = criterion["criterion_type"] == "inclusion"
            if call.role == "matcher_judge":
                label = "not enough information"
                basis = "unresolved_information"
                evidence = ()
            else:
                label = "included" if inclusion else "not excluded"
                basis = (
                    "direct_evidence"
                    if inclusion
                    else "expected_documentation_absence"
                )
                evidence = (0,) if inclusion else ()
            judgments.append(
                _judgment(
                    criterion["annotation_id"],
                    label,
                    basis,
                    evidence=evidence,
                )
            )
        if call.role == "trialgpt_architecture_single":
            response = ArchitectureSingleResponse.model_validate(
                {"judgments": judgments, "final_status": "eligible"}
            )
        elif call.role == "matcher_judge":
            response = ArchitectureMatcherResponse.model_validate(
                {"judgments": judgments}
            )
        else:
            response = ArchitectureReviewerResponse.model_validate(
                {"reviews": judgments}
            )
        return response, ModelUsage(
            model_id="scripted-local",
            effort="medium",
            input_tokens=10,
            output_tokens=5,
            latency_ms=1,
            finish_reason="stop",
        )


def test_case_has_one_frozen_gold_free_bm25_snapshot() -> None:
    case = build_architecture_case(_pair(), retrieval_top_k=2)

    assert case.pair_id == "sigir-synthetic/NCT-SYNTHETIC"
    assert [item.annotation_id for item in case.bm25_snapshot.criteria] == [0, 1]
    assert all(len(item.hits) == 2 for item in case.bm25_snapshot.criteria)
    serialized = case.model_dump_json()
    assert "expert_eligibility" not in serialized
    assert "gpt4_eligibility" not in serialized
    with pytest.raises(Exception):
        case.bm25_snapshot.top_k = 1


def test_explicit_pair_selection_preserves_requested_smoke_order() -> None:
    first = _pair("sigir-201512", "NCT02418169")
    second = _pair("sigir-20143", "NCT02490059")

    selected = select_architecture_pairs(
        [first, second],
        ["sigir-20143/NCT02490059", "sigir-201512/NCT02418169"],
    )

    assert [pair_id(item) for item in selected] == [
        "sigir-20143/NCT02490059",
        "sigir-201512/NCT02418169",
    ]
    with pytest.raises(ValueError, match="unknown"):
        select_architecture_pairs([first, second], ["missing/NCT0"])


def test_final_gold_rule_has_violation_then_nei_precedence() -> None:
    assert aggregate_trial_status(
        [("inclusion", "included"), ("exclusion", "not excluded")]
    ) is TrialFinalStatus.ELIGIBLE
    assert aggregate_trial_status(
        [("inclusion", "included"), ("exclusion", "not enough information")]
    ) is TrialFinalStatus.UNCERTAIN
    assert aggregate_trial_status(
        [("inclusion", "not included"), ("exclusion", "not enough information")]
    ) is TrialFinalStatus.INELIGIBLE


def test_review_trigger_selects_all_nei_and_never_selects_decisive_labels() -> None:
    ordinary = ArchitectureCriterionJudgment.model_validate(
        _judgment(0, "not enough information", "unresolved_information")
    )
    bounded = ArchitectureCriterionJudgment.model_validate(
        _judgment(
            1,
            "not enough information",
            "unresolved_information",
            flags=("strong_implicit_evidence_candidate",),
        )
    )
    conflict = ArchitectureCriterionJudgment.model_validate(
        _judgment(2, "not enough information", "conflicting_evidence")
    )
    decisive_flagged = ArchitectureCriterionJudgment.model_validate(
        _judgment(
            3,
            "included",
            "direct_evidence",
            evidence=(0,),
            flags=("matcher_requested",),
        )
    )
    decisive_conflict = ArchitectureCriterionJudgment.model_validate(
        _judgment(4, "not excluded", "conflicting_evidence")
    )

    selected = select_m2_review_targets(
        [ordinary, bounded, conflict, decisive_flagged, decisive_conflict]
    )

    assert selected == {
        0: ("initial_matcher_nei",),
        1: ("initial_matcher_nei",),
        2: ("initial_matcher_nei",),
    }


def test_reviewer_transition_guard_accepts_only_bounded_nei_changes() -> None:
    case = build_architecture_case(_pair(), retrieval_top_k=2)
    inclusion, exclusion = case.criteria
    initial_inclusion = ArchitectureCriterionJudgment.model_validate(
        _judgment(0, "not enough information", "unresolved_information")
    )
    initial_exclusion = ArchitectureCriterionJudgment.model_validate(
        _judgment(1, "not enough information", "unresolved_information")
    )

    validate_m2_review_transition(
        initial_inclusion,
        ArchitectureCriterionJudgment.model_validate(
            _judgment(0, "included", "strong_implicit_evidence", evidence=(0,))
        ),
        inclusion,
    )
    validate_m2_review_transition(
        initial_exclusion,
        ArchitectureCriterionJudgment.model_validate(
            _judgment(1, "not excluded", "expected_documentation_absence")
        ),
        exclusion,
    )
    with pytest.raises(ValueError, match="not applicable"):
        validate_m2_review_transition(
            initial_inclusion,
            ArchitectureCriterionJudgment.model_validate(
                _judgment(0, "not applicable", "not_applicable")
            ),
            inclusion,
        )
    with pytest.raises(ValueError, match="only supports not excluded"):
        validate_m2_review_transition(
            initial_inclusion,
            ArchitectureCriterionJudgment.model_validate(
                _judgment(0, "included", "expected_documentation_absence")
            ),
            inclusion,
        )
    with pytest.raises(ValueError, match="direct or strong implicit"):
        validate_m2_review_transition(
            initial_exclusion,
            ArchitectureCriterionJudgment.model_validate(
                _judgment(1, "excluded", "conflicting_evidence")
            ),
            exclusion,
        )


def test_runner_executes_real_arm_sequences_without_next_evidence_and_scores() -> None:
    model = _ArchitectureModel()
    checkpoints = []

    result = run_trialgpt_architecture_benchmark(
        [_pair()],
        model,
        retrieval_top_k=2,
        on_case_completed=lambda plan, arm_results: checkpoints.append(
            (plan, arm_results)
        ),
    )

    assert [call.role for call in model.calls] == [
        "trialgpt_architecture_single",
        "matcher_judge",
        "matcher_judge",
        "selective_reviewer",
    ]
    assert len(checkpoints) == 1
    assert tuple(item.arm for item in checkpoints[0][1]) == checkpoints[0][0].arm_order
    assert all(item.score is None for item in checkpoints[0][1])
    by_arm = {item.arm: item for item in result.results}
    assert by_arm[ArchitectureArm.S1].final_status is TrialFinalStatus.ELIGIBLE
    assert by_arm[ArchitectureArm.M1].final_status is TrialFinalStatus.UNCERTAIN
    assert by_arm[ArchitectureArm.M2].final_status is TrialFinalStatus.ELIGIBLE
    assert by_arm[ArchitectureArm.M2].review_selected_ids == (1,)
    assert by_arm[ArchitectureArm.M2].review_changed_count == 1
    assert all(item.next_evidence_model_calls == 0 for item in result.results)
    assert result.next_evidence_model_calls == 0
    assert result.arm_role_call_counts["M1"]["coordinator"] == 0
    assert result.arm_role_call_counts["M1"]["matcher_judge"] == 1
    assert result.arm_role_call_counts["M2"]["selective_reviewer"] == 1
    assert result.arm_role_call_counts["M2"]["next_evidence"] == 0
    assert result.arm_metrics["S1"].criterion_accuracy == 1.0
    assert result.arm_metrics["M1"].criterion_accuracy == 0.5
    assert result.arm_metrics["M2"].pre_review_criterion_accuracy == 0.5
    assert result.arm_metrics["M2"].criterion_accuracy == 1.0
    assert result.arm_metrics["M2"].review_accuracy_delta == 0.5
    assert result.arm_metrics["M2"].review_wrong_to_correct == 1
    assert result.arm_metrics["M2"].review_correct_to_wrong == 0
    assert by_arm[ArchitectureArm.M2].pre_review_predictions[1].eligibility_label == (
        "not enough information"
    )
    assert result.protocol_id == STATIC_ARCHITECTURE_PROTOCOL_ID
    assert result.static_coordinator_rule_id == STATIC_COORDINATOR_RULE_ID
    assert all(
        item.judgment_batching_id == JUDGMENT_BATCHING_ID
        and item.judgment_batch_count == 1
        for item in result.results
    )
    assert all(
        ":batch-" not in record.call_id
        for item in result.results
        for record in item.calls
    )
    legacy_result = by_arm[ArchitectureArm.S1].model_dump(mode="json")
    legacy_result.pop("judgment_batching_id")
    legacy_result.pop("judgment_batch_count")
    legacy_result.pop("protocol_id")
    legacy_result.pop("pre_review_predictions")
    legacy_result.pop("static_coordinator_rule_id")
    restored = ArchitectureArmResult.model_validate(legacy_result)
    assert restored.judgment_batching_id == JUDGMENT_BATCHING_ID
    assert restored.judgment_batch_count == 1
    assert restored.protocol_id == "trialgpt-static-architecture-v1"
    assert restored.pre_review_predictions == ()
    assert restored.static_coordinator_rule_id is None
    m1_m2 = next(
        item
        for item in result.paired_metrics
        if item.arm_a is ArchitectureArm.M1 and item.arm_b is ArchitectureArm.M2
    )
    assert m1_m2.wrong_to_correct == 1
    assert m1_m2.trial_wrong_to_correct == 1

    shared_payloads = [
        call.payload["shared_input"]
        for call in model.calls
        if call.role in {"trialgpt_architecture_single", "matcher_judge"}
    ]
    assert shared_payloads[0] == shared_payloads[1] == shared_payloads[2]
    completed_events = [
        event
        for arm_result in result.results
        for event in arm_result.trace
        if event.event == "structured_model_completed"
    ]
    assert Counter(event.actor for event in completed_events) == {
        "trialgpt_architecture_single": 1,
        "matcher_judge": 2,
        "selective_reviewer": 1,
    }
    route_events = [
        event
        for arm_result in result.results
        for event in arm_result.trace
        if event.event == "deterministic_route_selected"
    ]
    assert len(route_events) == 2
    assert all(event.output["model_calls"] == 0 for event in route_events)
    assert all(record.usage is not None for item in result.results for record in item.calls)


def test_large_case_uses_three_ordered_judgment_and_review_batches() -> None:
    pair = _large_pair(41)
    case = build_architecture_case(pair, retrieval_top_k=2)

    batches = _judgment_batches(case)

    assert MAX_CRITERIA_PER_JUDGMENT_CALL == 19
    assert [len(batch.criteria) for batch in batches] == [19, 19, 3]
    assert [
        item.annotation_id for batch in batches for item in batch.criteria
    ] == list(range(41))
    assert all(
        [item.annotation_id for item in batch.criteria]
        == [item.annotation_id for item in batch.bm25_snapshot.criteria]
        for batch in batches
    )

    model = _LargeArchitectureModel()
    result = run_trialgpt_architecture_benchmark(
        [pair], model, retrieval_top_k=2
    )
    by_arm = {item.arm: item for item in result.results}

    assert all(item.status is RunStatus.COMPLETED for item in result.results)
    assert all(item.judgment_batch_count == 3 for item in result.results)
    assert by_arm[ArchitectureArm.S1].reported_final_status is None
    assert [
        item.annotation_id for item in by_arm[ArchitectureArm.S1].predictions
    ] == list(range(41))
    assert result.arm_role_call_counts["S1"]["trialgpt_architecture_single"] == 3
    assert result.arm_role_call_counts["M1"]["coordinator"] == 0
    assert result.arm_role_call_counts["M1"]["matcher_judge"] == 3
    assert result.arm_role_call_counts["M2"]["coordinator"] == 0
    assert result.arm_role_call_counts["M2"]["matcher_judge"] == 3
    assert result.arm_role_call_counts["M2"]["selective_reviewer"] == 3
    constraint_events = [
        event
        for item in result.results
        for event in item.trace
        if event.event == "static_dataset_constraints"
    ]
    assert all(
        event.output["judgment_batching_id"] == JUDGMENT_BATCHING_ID
        and event.output["judgment_batch_count"] == 3
        for event in constraint_events
    )


def test_balanced_hash_plan_is_input_order_independent() -> None:
    base = build_architecture_case(_pair())
    cases = [
        base.model_copy(update={"case_id": f"case-{index}", "pair_id": f"p-{index}"})
        for index in range(8)
    ]

    first = plan_architecture_arm_orders(cases, seed=7)
    second = plan_architecture_arm_orders(list(reversed(cases)), seed=7)

    assert first == second
    counts = Counter(tuple(item.value for item in plan.arm_order) for plan in first)
    assert max(counts.values()) - min(counts.values()) <= 1


def test_case_checkpoint_results_can_be_assembled_without_new_model_calls() -> None:
    pair = _pair()
    case = build_architecture_case(pair, retrieval_top_k=2)
    plan = plan_architecture_arm_orders([case], seed=7)[0]
    model = _ArchitectureModel()

    raw_results = run_trialgpt_architecture_case(case, plan, model)
    calls_after_case = len(model.calls)
    result = assemble_trialgpt_architecture_benchmark(
        [pair],
        [plan],
        raw_results,
        order_seed=7,
        retrieval_top_k=2,
    )

    assert len(model.calls) == calls_after_case
    assert result.arm_metrics["S1"].criterion_accuracy == 1.0
    assert result.arm_metrics["M2"].trial_status_accuracy == 1.0


def test_case_resume_reuses_completed_s1_m1_and_runs_only_failed_m2() -> None:
    case = build_architecture_case(_pair(), retrieval_top_k=2)
    plan = plan_architecture_arm_orders([case], seed=7)[0]
    initial = run_trialgpt_architecture_case(case, plan, _ArchitectureModel())
    partial = tuple(
        item
        if item.arm is not ArchitectureArm.M2
        else item.model_copy(update={"status": RunStatus.FAILED})
        for item in initial
    )
    retry_model = _ArchitectureModel()

    merged = run_trialgpt_architecture_case(
        case,
        plan,
        retry_model,
        prior_results=partial,
    )

    assert tuple(item.arm for item in merged) == plan.arm_order
    assert [call.role for call in retry_model.calls] == [
        "matcher_judge",
        "selective_reviewer",
    ]
    previous_by_arm = {item.arm: item for item in partial}
    merged_by_arm = {item.arm: item for item in merged}
    assert merged_by_arm[ArchitectureArm.S1] == previous_by_arm[ArchitectureArm.S1]
    assert merged_by_arm[ArchitectureArm.M1] == previous_by_arm[ArchitectureArm.M1]
    assert merged_by_arm[ArchitectureArm.M2].status is RunStatus.COMPLETED


def test_schema_failure_is_recorded_and_other_arms_continue() -> None:
    class _SingleFailureModel(_ArchitectureModel):
        def complete(self, call: ModelCall):
            if call.role == "trialgpt_architecture_single":
                self.calls.append(call)
                raise ValueError("synthetic schema failure")
            return super().complete(call)

    result = run_trialgpt_architecture_benchmark([_pair()], _SingleFailureModel())
    by_arm = {item.arm: item for item in result.results}

    assert by_arm[ArchitectureArm.S1].status is RunStatus.FAILED
    assert by_arm[ArchitectureArm.S1].failure_stage == "single"
    assert by_arm[ArchitectureArm.M1].status is RunStatus.COMPLETED
    assert by_arm[ArchitectureArm.M2].status is RunStatus.COMPLETED
    assert by_arm[ArchitectureArm.S1].score is None
