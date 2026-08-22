from __future__ import annotations

import json
from typing import Any

from clarifytrial.contracts import AgentAction, NextAction
from clarifytrial.interactive import (
    AuthoredOrderPolicy,
    ClarifyTrialRulePolicy,
    ClarifyTrialExactCoveragePolicy,
    ExactDecisionTreePolicy,
    ExactPolicyObjective,
    WidestImpactPolicy,
    build_binary_scenarios,
    build_uniform_binary_scenarios,
    exact_fact_sensitivity,
    ModelQuestionPolicy,
    NoQuestionPolicy,
    build_interactive_pilot_cases,
    minimal_sufficient_fact_sets,
    run_interactive_policy,
    summarize_interactive_runs,
)
from clarifytrial.interactive.contracts import InteractiveCase
from clarifytrial.interactive.oracle import evaluate_interactive_case
from clarifytrial.interactive.pilot import run_interactive_pilot
from clarifytrial.llm.base import ModelCall, ModelUsage


def test_pilot_has_declared_size_balance_and_answer_free_public_view() -> None:
    cases = build_interactive_pilot_cases()

    assert len(cases) == 12
    assert sorted({item.disease_group for item in cases}) == [
        "2형 당뇨병",
        "유방암",
        "주요우울장애",
    ]
    assert all(len(item.trials) == 5 for item in cases)
    assert all(len(item.hidden_facts) == 5 for item in cases)
    assert all(item.action_budget == 3 for item in cases)
    assert all(
        len([candidate for candidate in cases if candidate.disease_group == group])
        == 4
        for group in {item.disease_group for item in cases}
    )

    case = cases[0]
    private_values = {
        item.answer.evidence.statement for item in case.hidden_facts
    }
    public_text = json.dumps(
        case.public_policy_view().model_dump(mode="json"), ensure_ascii=False
    )
    assert all(value not in public_text for value in private_values)
    assert "value" not in public_text
    assert "evidence_id" not in public_text
    assert "synthetic-official_verification" not in public_text


def test_oracle_finds_the_three_fact_minimal_set_without_answer_leakage() -> None:
    case = build_interactive_pilot_cases()[0]

    gold = minimal_sufficient_fact_sets(case)

    assert gold.recoverable_within_budget
    assert len(gold.minimal_fact_sets) == 1
    assert len(gold.minimal_fact_sets[0]) == 3
    assert gold.minimal_fact_sets[0] == sorted(
        [
            f"{case.case_id}-egfr",
            f"{case.case_id}-hba1c",
            f"{case.case_id}-injection",
        ]
    )

    sensitivity = exact_fact_sensitivity(case)
    assert sensitivity.evaluated_state_count == 32
    by_id = {item.fact_id: item for item in sensitivity.facts}
    assert by_id[f"{case.case_id}-hba1c"].average_marginal_recovery > 0
    assert by_id[f"{case.case_id}-egfr"].average_marginal_recovery > 0
    assert by_id[f"{case.case_id}-injection"].average_marginal_recovery > 0
    assert by_id[f"{case.case_id}-bmi"].average_marginal_recovery == 0
    assert by_id[f"{case.case_id}-stable_med"].average_marginal_recovery == 0


def test_dynamic_rule_recovers_oracle_and_authored_order_does_not() -> None:
    case = build_interactive_pilot_cases()[0]

    no_questions = run_interactive_policy(case, NoQuestionPolicy())
    authored = run_interactive_policy(case, AuthoredOrderPolicy())
    dynamic = run_interactive_policy(case, ClarifyTrialRulePolicy())

    assert no_questions.metrics.trial_status_recovery == 0
    assert authored.metrics.trial_status_recovery == 0.2
    assert authored.metrics.unnecessary_action_count == 2
    assert dynamic.metrics.trial_status_recovery == 1
    assert dynamic.metrics.necessary_fact_recall == 1
    assert dynamic.metrics.unnecessary_action_count == 0
    assert dynamic.metrics.realized_impact_capture == 1
    assert len(dynamic.action_history) == 3


def test_exact_coverage_policy_uses_the_remaining_budget_without_hidden_answers() -> None:
    case = build_interactive_pilot_cases()[0]

    result = run_interactive_policy(case, ClarifyTrialExactCoveragePolicy())

    assert result.metrics.trial_status_recovery == 1
    assert result.metrics.unnecessary_action_count == 0
    assert result.metrics.premature_confirmations == 0
    assert len(result.action_history) == 3


def test_exact_tree_uses_all_binary_scenarios_without_actual_answer_marker() -> None:
    case = build_interactive_pilot_cases()[0]
    distribution = build_uniform_binary_scenarios(case)
    policy = ExactDecisionTreePolicy(
        case.public_policy_view(),
        case.initial_patient_state(),
        distribution,
        ExactPolicyObjective.EXPECTED,
    )

    run = run_interactive_policy(case, policy)

    assert len(distribution.scenarios) == 32
    assert sum(item.probability for item in distribution.scenarios) == 1
    serialized = json.dumps(distribution.model_dump(mode="json"), ensure_ascii=False)
    assert all(
        item.answer.evidence.statement not in serialized for item in case.hidden_facts
    )
    assert all(
        item.answer.evidence.evidence_id not in serialized
        for item in case.hidden_facts
    )
    assert all(
        item.answer.evidence.source_location not in serialized
        for item in case.hidden_facts
    )
    assert run.metrics.trial_status_recovery == 1
    assert run.metrics.unnecessary_action_count == 0
    assert policy.choices[0].evaluated_states == 131
    assert policy.choices[0].value.average_trial_status_recovery == 0.8


def test_exact_tree_can_beat_public_coverage_when_answers_are_imbalanced() -> None:
    source = build_interactive_pilot_cases()[0]
    payload = source.model_dump(mode="json")
    payload["trials"] = payload["trials"][2:]
    payload["action_budget"] = 1
    case = InteractiveCase.model_validate(payload)
    probabilities = {
        f"{case.case_id}-hba1c": 0.01,
        f"{case.case_id}-egfr": 0.01,
    }
    distribution = build_binary_scenarios(case, probabilities)
    view = case.public_policy_view()
    snapshot = evaluate_interactive_case(case, case.initial_patient_state())

    exact = ExactDecisionTreePolicy(
        view,
        case.initial_patient_state(),
        distribution,
        ExactPolicyObjective.EXPECTED,
    )
    exact_action = exact.select(view, snapshot, frozenset())
    coverage_action = WidestImpactPolicy().select(view, snapshot, frozenset())

    assert exact_action.target_fact_id == f"{case.case_id}-injection"
    assert coverage_action.target_fact_id == f"{case.case_id}-bmi"
    assert exact.choices[0].value.average_trial_status_recovery > 0


def test_worst_case_tree_protects_the_minimum_before_average_recovery() -> None:
    source = build_interactive_pilot_cases()[0]
    injection_trial = source.trials[2]
    first_joint = source.trials[3]
    second_joint_id = f"{source.case_id}-second-joint"
    second_joint = first_joint.model_copy(
        update={
            "trial_id": second_joint_id,
            "criteria": [
                first_joint.criteria[0].model_copy(
                    update={
                        "criterion_id": f"{second_joint_id}-c1",
                        "trial_id": second_joint_id,
                    }
                ),
                source.trials[4].criteria[1].model_copy(
                    update={
                        "criterion_id": f"{second_joint_id}-c2",
                        "trial_id": second_joint_id,
                    }
                ),
            ],
        }
    )
    kept_codes = {"injection", "hba1c", "bmi", "stable_med"}
    hidden = [
        item
        for item in source.hidden_facts
        if item.request.fact_id.rsplit("-", 1)[-1] in kept_codes
    ]
    kept_evidence_ids = {
        *source.initial_visible_evidence_ids,
        *(item.answer.evidence.evidence_id for item in hidden),
    }
    case = InteractiveCase(
        case_id=source.case_id,
        disease_group=source.disease_group,
        full_patient_state=source.full_patient_state.model_copy(
            update={
                "facts": [
                    item
                    for item in source.full_patient_state.facts
                    if item.evidence_id in kept_evidence_ids
                ]
            }
        ),
        initial_visible_evidence_ids=source.initial_visible_evidence_ids,
        trials=[injection_trial, first_joint, second_joint],
        hidden_facts=hidden,
        action_budget=1,
    )
    distribution = build_binary_scenarios(
        case, {f"{case.case_id}-hba1c": 0.01}
    )
    view = case.public_policy_view()
    snapshot = evaluate_interactive_case(case, case.initial_patient_state())

    expected = ExactDecisionTreePolicy(
        view,
        case.initial_patient_state(),
        distribution,
        ExactPolicyObjective.EXPECTED,
    )
    robust = ExactDecisionTreePolicy(
        view,
        case.initial_patient_state(),
        distribution,
        ExactPolicyObjective.WORST_CASE,
    )

    expected_action = expected.select(view, snapshot, frozenset())
    robust_action = robust.select(view, snapshot, frozenset())

    assert expected_action.target_fact_id == f"{case.case_id}-hba1c"
    assert robust_action.target_fact_id == f"{case.case_id}-injection"
    assert expected.choices[0].value.average_trial_status_recovery > 0.6
    assert robust.choices[0].value.worst_case_trial_status_recovery == 1 / 3


class _PublicOnlyModel:
    def __init__(self) -> None:
        self.payloads: list[dict[str, Any]] = []

    def complete(self, call: ModelCall[Any]):
        assert call.prompt_id == "prompts/interactive_question_selector.md"
        payload = dict(call.payload)
        self.payloads.append(payload)
        available = payload["available_information"]
        selected = min(
            available,
            key=lambda item: (
                -item["currently_unresolved_related_trials"],
                -item["currently_unresolved_related_criteria"],
                item["route_cost"],
                item["fact_id"],
            ),
        )
        action = NextAction(selected["available_actions"][0])
        public = selected["fact_id"]
        return (
            AgentAction(
                action=action,
                target_fact_id=public,
                related_criterion_ids=selected["related_criterion_ids"],
                reason="Select the largest current impact.",
                message=(
                    selected["description"]
                    if action
                    in {NextAction.ASK_PATIENT, NextAction.REQUEST_VERIFICATION}
                    else None
                ),
            ),
            ModelUsage(
                model_id="fake-sol-medium",
                effort="medium",
                input_tokens=100,
                output_tokens=20,
                thinking_tokens=10,
                total_tokens=130,
            ),
        )


def test_model_policy_receives_no_private_answer_and_usage_is_summed() -> None:
    model = _PublicOnlyModel()
    run = run_interactive_policy(
        build_interactive_pilot_cases()[0], ModelQuestionPolicy(model)
    )

    serialized = json.dumps(model.payloads, ensure_ascii=False)
    assert "synthetic-official_verification" not in serialized
    assert "answer" not in serialized
    assert "patient_state" not in serialized
    assert run.metrics.trial_status_recovery == 1
    summary = summarize_interactive_runs([run])
    assert summary.total_input_tokens == 300
    assert summary.total_output_tokens == 60
    assert summary.total_reasoning_tokens == 30
    assert summary.total_tokens == 390


def test_pilot_writer_keeps_plan_runs_and_summary_separate(tmp_path) -> None:
    summary_path = run_interactive_pilot(tmp_path)

    assert summary_path == tmp_path / "summary.json"
    plan = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    rows = (tmp_path / "runs.jsonl").read_text(encoding="utf-8").splitlines()
    assert plan["case_count"] == 12
    assert plan["hidden_facts_per_case"] == 5
    assert "연구용 시제품" in plan["medical_disclaimer"]
    assert summary["run_count"] == 96
    assert "임상시험 참가 가능성을 확정" in summary["medical_disclaimer"]
    assert len(rows) == 96
