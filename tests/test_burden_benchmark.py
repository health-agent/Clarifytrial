from __future__ import annotations

import json
from pathlib import Path

from clarifytrial.interactive import (
    AcquisitionMode,
    AcquisitionPolicyId,
    ActionStatus,
    AvailabilityStructure,
    benchmark_patient_profiles,
    build_acquisition_catalog,
    build_route_choice_catalog,
    build_guidance_output,
    build_patient_burden_profile,
    build_public_case,
    load_public_benchmark_spec,
    run_burden_policy,
    run_public_burden_benchmark,
    select_acquisition_option,
)
from clarifytrial.interactive.burden_contracts import (
    AcquisitionOption,
    DirectCostBand,
    PatientInputStatus,
    PreferenceMode,
)
from clarifytrial.interactive.burden_policy import (
    current_fact_impacts,
    explicit_limit_violations,
)
from clarifytrial.contracts import NextAction
from clarifytrial.interactive.oracle import evaluate_interactive_case
from clarifytrial.interactive.stress import build_stress_case


CONFIG_PATH = Path("configs/interactive_public_benchmark_v1.json")


def _fake_source_cache(destination: Path) -> Path:
    spec = load_public_benchmark_spec(CONFIG_PATH)
    records = destination / "records"
    records.mkdir(parents=True)
    for group in spec.groups:
        for trial in group.trials:
            record = {
                "protocolSection": {
                    "identificationModule": {"nctId": trial.nct_id},
                    "eligibilityModule": {
                        "eligibilityCriteria": "\n".join(
                            criterion.source_statement
                            for criterion in trial.criteria
                        )
                    },
                }
            }
            (records / f"{trial.nct_id}.json").write_text(
                json.dumps(record), encoding="utf-8"
            )
    return destination


def _first_case():
    spec = load_public_benchmark_spec(CONFIG_PATH)
    group = spec.groups[0]
    profile = group.profiles[0]
    mask = group.masks[0]
    return group, profile, mask, build_public_case(group, profile, mask)


def test_absent_and_partial_patient_input_never_become_zero() -> None:
    absent = build_patient_burden_profile("absent")
    partial = build_patient_burden_profile(
        "partial", {"cost_sensitivity_0_to_3": 3}
    )

    assert absent.input_status is PatientInputStatus.ABSENT
    assert absent.preference_mode is PreferenceMode.BALANCED
    assert absent.cost_sensitivity_0_to_3 is None
    assert absent.travel_constraint_0_to_3 is None
    assert "cost_sensitivity_0_to_3" in absent.defaulted_fields
    assert partial.input_status is PatientInputStatus.PARTIAL
    assert partial.cost_sensitivity_0_to_3 == 3
    assert partial.travel_constraint_0_to_3 is None
    assert "cost_sensitivity_0_to_3" not in partial.defaulted_fields
    assert "travel_constraint_0_to_3" in partial.defaulted_fields


def test_existing_result_is_selected_before_a_new_test_for_the_same_fact() -> None:
    _, _, _, case = _first_case()
    full_view = case.public_policy_view()
    target = next(
        item for item in full_view.available_information if item.fact_id.endswith("-hba1c")
    )
    view = full_view.model_copy(update={"available_information": [target]})
    snapshot = evaluate_interactive_case(case, case.initial_patient_state())
    profile = build_patient_burden_profile("default")

    existing = build_acquisition_catalog(
        view, AvailabilityStructure.EXISTING_DATA_CENTERED
    )
    existing_choice = select_acquisition_option(
        view=view,
        snapshot=snapshot,
        revealed_fact_ids=frozenset(),
        catalog=existing,
        profile=profile,
        policy_id=AcquisitionPolicyId.PATIENT_ADAPTIVE,
    )
    assert existing_choice.selected_option is not None
    assert (
        existing_choice.selected_option.acquisition_mode
        is AcquisitionMode.EXISTING_OFFICIAL_RESULT
    )
    assert not any(
        "기존 자료 경로 우선" in item.reason
        for item in existing_choice.decision_trace.removed_options
    )
    assert any(
        "다른 경로보다 모든 부담" in item.reason
        for item in existing_choice.decision_trace.removed_options
    )

    new_needed = build_acquisition_catalog(
        view, AvailabilityStructure.NEW_CONFIRMATION_NEEDED
    )
    new_choice = select_acquisition_option(
        view=view,
        snapshot=snapshot,
        revealed_fact_ids=frozenset(),
        catalog=new_needed,
        profile=profile,
        policy_id=AcquisitionPolicyId.PATIENT_ADAPTIVE,
    )
    assert new_choice.selected_option is not None
    assert new_choice.selected_option.new_test_required is True
    assert new_choice.action_status is ActionStatus.AWAITING_CLINICIAN_AUTHORIZATION


def test_controlled_route_choice_uses_patient_preferences_after_impact() -> None:
    _, _, _, case = _first_case()
    full_view = case.public_policy_view()
    target = full_view.available_information[0]
    view = full_view.model_copy(update={"available_information": [target]})
    snapshot = evaluate_interactive_case(case, case.initial_patient_state())
    catalog = build_route_choice_catalog(view)
    profiles = {item.profile_id: item for item in benchmark_patient_profiles()}

    choices = {
        profile_id: select_acquisition_option(
            view=view,
            snapshot=snapshot,
            revealed_fact_ids=frozenset(),
            catalog=catalog,
            profile=profile,
            policy_id=AcquisitionPolicyId.PATIENT_ADAPTIVE,
        )
        for profile_id, profile in profiles.items()
    }

    assert (
        choices["low_extra_burden"].selected_option.acquisition_mode
        is AcquisitionMode.EXISTING_OFFICIAL_RESULT
    )
    assert (
        choices["mobility_cost_constrained"].selected_option.acquisition_mode
        is AcquisitionMode.EXISTING_OFFICIAL_RESULT
    )
    assert (
        choices["time_urgent"].selected_option.acquisition_mode
        is AcquisitionMode.NEW_NONINVASIVE_TEST
    )
    assert choices["time_urgent"].selected_option.expected_delay_hours == 8
    assert choices["low_extra_burden"].selected_option.expected_delay_hours == 72


def test_default_patient_policy_uses_current_impact_before_route_preference() -> None:
    case = build_stress_case(
        "fully_separated",
        0,
        seed=20260830,
        action_budget=3,
    )
    view = case.public_policy_view()
    snapshot = evaluate_interactive_case(case, case.initial_patient_state())
    catalog = build_acquisition_catalog(
        view,
        AvailabilityStructure.EXISTING_DATA_CENTERED,
    )
    profile = benchmark_patient_profiles()[0]

    current = select_acquisition_option(
        view=view,
        snapshot=snapshot,
        revealed_fact_ids=frozenset(),
        catalog=catalog,
        profile=profile,
        policy_id=AcquisitionPolicyId.PATIENT_ADAPTIVE,
    )
    impact = select_acquisition_option(
        view=view,
        snapshot=snapshot,
        revealed_fact_ids=frozenset(),
        catalog=catalog,
        profile=profile,
        policy_id=AcquisitionPolicyId.IMPACT_ONLY,
    )
    exact = select_acquisition_option(
        view=view,
        snapshot=snapshot,
        revealed_fact_ids=frozenset(),
        catalog=catalog,
        profile=profile,
        policy_id=AcquisitionPolicyId.EXACT_FIXED_ROUTE,
    )

    assert current.selected_option is not None
    assert impact.selected_option is not None
    assert exact.selected_option is not None
    assert "시험" in current.selection_reason
    assert "조건" in current.selection_reason
    current_impact = current_fact_impacts(
        view,
        snapshot,
        frozenset(),
    )[current.selected_option.fact_id]
    impact_impact = current_fact_impacts(
        view,
        snapshot,
        frozenset(),
    )[impact.selected_option.fact_id]

    # The current policy may use route burden to break a tie between facts with
    # the same reach.  It must not sacrifice reach to imitate the exact planner.
    assert current_impact[:2] == impact_impact[:2]
    assert current.selected_option.fact_id != exact.selected_option.fact_id


def test_limit_only_ablation_changes_filtering_not_the_unconstrained_order() -> None:
    _, _, _, case = _first_case()
    view = case.public_policy_view()
    snapshot = evaluate_interactive_case(case, case.initial_patient_state())
    catalog = build_acquisition_catalog(
        view, AvailabilityStructure.NEW_CONFIRMATION_NEEDED
    )
    no_limits = benchmark_patient_profiles()[0]

    fixed = select_acquisition_option(
        view=view,
        snapshot=snapshot,
        revealed_fact_ids=frozenset(),
        catalog=catalog,
        profile=no_limits,
        policy_id=AcquisitionPolicyId.EXACT_FIXED_ROUTE,
    )
    limits_only_without_limits = select_acquisition_option(
        view=view,
        snapshot=snapshot,
        revealed_fact_ids=frozenset(),
        catalog=catalog,
        profile=no_limits,
        policy_id=AcquisitionPolicyId.PATIENT_LIMITS_ONLY,
    )

    assert fixed.selected_option == limits_only_without_limits.selected_option

    constrained = benchmark_patient_profiles()[1]
    limits_only = select_acquisition_option(
        view=view,
        snapshot=snapshot,
        revealed_fact_ids=frozenset(),
        catalog=catalog,
        profile=constrained,
        policy_id=AcquisitionPolicyId.PATIENT_LIMITS_ONLY,
    )
    assert limits_only.selected_option is None or not explicit_limit_violations(
        limits_only.selected_option, constrained
    )


def test_limit_ablation_uses_the_same_order_and_changes_only_forbidden_paths() -> None:
    group, base, mask, case = _first_case()

    def run(profile_index, availability, policy):
        return run_burden_policy(
            case=case,
            base_profile_id=base.profile_id,
            split=base.split,
            mask_id=mask.mask_id,
            patient_profile=benchmark_patient_profiles()[profile_index],
            availability=availability,
            policy_id=policy,
        )

    for profile_index, availability in (
        (0, AvailabilityStructure.EXISTING_DATA_CENTERED),
        (0, AvailabilityStructure.NEW_CONFIRMATION_NEEDED),
        (1, AvailabilityStructure.EXISTING_DATA_CENTERED),
    ):
        fixed = run(
            profile_index,
            availability,
            AcquisitionPolicyId.EXACT_FIXED_ROUTE,
        )
        limits = run(
            profile_index,
            availability,
            AcquisitionPolicyId.PATIENT_LIMITS_ONLY,
        )
        assert fixed.selected_fact_ids == limits.selected_fact_ids
        assert fixed.selected_option_ids == limits.selected_option_ids
        assert fixed.metrics == limits.metrics

    fixed = run(
        1,
        AvailabilityStructure.NEW_CONFIRMATION_NEEDED,
        AcquisitionPolicyId.EXACT_FIXED_ROUTE,
    )
    limits = run(
        1,
        AvailabilityStructure.NEW_CONFIRMATION_NEEDED,
        AcquisitionPolicyId.PATIENT_LIMITS_ONLY,
    )
    assert fixed.metrics.explicit_limit_violations > 0
    assert fixed.metrics.new_test_count > 0
    assert fixed.metrics.additional_visit_count > 0
    assert limits.metrics.explicit_limit_violations == 0
    assert limits.metrics.new_test_count == 0
    assert limits.metrics.additional_visit_count == 0
    assert {
        tuple(item.decision.decision_trace.applied_ordering_rule)
        for item in fixed.action_history
    } == {
        tuple(item.decision.decision_trace.applied_ordering_rule)
        for item in limits.action_history
    }


def test_unknown_cost_against_an_explicit_limit_requires_patient_choice() -> None:
    _, _, _, case = _first_case()
    view = case.public_policy_view()
    snapshot = evaluate_interactive_case(case, case.initial_patient_state())
    profile = benchmark_patient_profiles()[1]
    catalog = build_acquisition_catalog(
        view, AvailabilityStructure.NEW_CONFIRMATION_NEEDED
    )
    catalog = {
        fact_id: tuple(
            option.model_copy(update={"direct_cost_band": DirectCostBand.UNKNOWN})
            if option.acquisition_mode is AcquisitionMode.OUTSIDE_RECORD
            else option
            for option in options
        )
        for fact_id, options in catalog.items()
    }

    choice = select_acquisition_option(
        view=view,
        snapshot=snapshot,
        revealed_fact_ids=frozenset(),
        catalog=catalog,
        profile=profile,
        policy_id=AcquisitionPolicyId.PATIENT_ADAPTIVE,
    )

    assert choice.selected_option is not None
    assert choice.selected_option.acquisition_mode is AcquisitionMode.OUTSIDE_RECORD
    assert choice.action_status is ActionStatus.AWAITING_PATIENT_CHOICE
    assert "direct_cost_band" in choice.decision_trace.unresolved_unknown_fields


def test_patient_adaptive_uses_current_impact_before_route_preferences() -> None:
    _, _, _, case = _first_case()
    view = case.public_policy_view()
    snapshot = evaluate_interactive_case(case, case.initial_patient_state())
    profile = benchmark_patient_profiles()[0]
    catalog = build_acquisition_catalog(
        view, AvailabilityStructure.EXISTING_DATA_CENTERED
    )

    adaptive = select_acquisition_option(
        view=view,
        snapshot=snapshot,
        revealed_fact_ids=frozenset(),
        catalog=catalog,
        profile=profile,
        policy_id=AcquisitionPolicyId.PATIENT_ADAPTIVE,
    )
    exact_comparator = select_acquisition_option(
        view=view,
        snapshot=snapshot,
        revealed_fact_ids=frozenset(),
        catalog=catalog,
        profile=profile,
        policy_id=AcquisitionPolicyId.EXACT_FIXED_ROUTE,
    )

    assert adaptive.decision_trace.applied_ordering_rule[:2] == [
        "affected_trials:max",
        "affected_criteria:max",
    ]
    assert (
        "exact_coverage_choice:max"
        not in adaptive.decision_trace.applied_ordering_rule
    )
    assert exact_comparator.decision_trace.applied_ordering_rule[:3] == [
        "exact_coverage_choice:max",
        "affected_trials:max",
        "affected_criteria:max",
    ]


def test_guidance_uses_the_same_fact_and_trial_ids_in_both_views() -> None:
    _, _, _, case = _first_case()
    view = case.public_policy_view()
    snapshot = evaluate_interactive_case(case, case.initial_patient_state())
    profile = benchmark_patient_profiles()[0]
    catalog = build_acquisition_catalog(
        view, AvailabilityStructure.EXISTING_DATA_CENTERED
    )
    decision = select_acquisition_option(
        view=view,
        snapshot=snapshot,
        revealed_fact_ids=frozenset(),
        catalog=catalog,
        profile=profile,
        policy_id=AcquisitionPolicyId.PATIENT_ADAPTIVE,
    )

    guidance = build_guidance_output(
        case=case,
        view=view,
        snapshot=snapshot,
        profile=profile,
        decision=decision,
        catalog=catalog,
        revealed_fact_ids=frozenset(),
        stop_reason=None,
    )

    assert guidance.selected_option is not None
    assert guidance.patient_message.fact_id == guidance.selected_option.fact_id
    assert (
        guidance.patient_message.affected_trial_ids
        == guidance.selected_option.affected_trial_ids
    )
    assert guidance.medical_disclaimer.startswith(
        "이 결과는 의학적 조언이 아닌 참고용입니다."
    )
    patient_json = json.dumps(
        guidance.patient_message.model_dump(mode="json"), ensure_ascii=False
    )
    assert "not_confirmed" not in patient_json
    assert '"retain"' not in patient_json
    assert "REQUEST_VERIFICATION" not in patient_json

    absent_profile = build_patient_burden_profile("absent")
    absent_decision = select_acquisition_option(
        view=view,
        snapshot=snapshot,
        revealed_fact_ids=frozenset(),
        catalog=catalog,
        profile=absent_profile,
        policy_id=AcquisitionPolicyId.PATIENT_ADAPTIVE,
    )
    absent_guidance = build_guidance_output(
        case=case,
        view=view,
        snapshot=snapshot,
        profile=absent_profile,
        decision=absent_decision,
        catalog=catalog,
        revealed_fact_ids=frozenset(),
        stop_reason=None,
    )
    assert absent_guidance.trial_groups == guidance.trial_groups
    assert absent_guidance.patient_input_status is PatientInputStatus.ABSENT
    assert absent_guidance.defaulted_fields
    assert any(
        "입력 없음" in item
        for item in absent_guidance.patient_message.applied_patient_settings
    )


def test_invasive_or_treatment_change_path_cannot_be_recommended_without_approval() -> None:
    _, _, _, case = _first_case()
    full_view = case.public_policy_view()
    target = full_view.available_information[0]
    view = full_view.model_copy(update={"available_information": [target]})
    snapshot = evaluate_interactive_case(case, case.initial_patient_state())
    invasive = AcquisitionOption(
        option_id=f"{target.fact_id}:invasive-test-fixture",
        fact_id=target.fact_id,
        action=NextAction.REQUEST_VERIFICATION,
        acquisition_mode=AcquisitionMode.NEW_INVASIVE_OR_TREATMENT_CHANGE,
        available_now=True,
        expected_delay_hours=72,
        visit_required=True,
        direct_cost_band=DirectCostBand.HIGH,
        physical_burden_0_to_3=3,
        emotional_burden_0_to_3=2,
        medical_risk_0_to_3=2,
        treatment_disruption_0_to_3=2,
        new_test_required=False,
        requires_patient_choice=True,
        requires_clinician_authorization=True,
        source_note="합성 승인 경계 검사",
    )
    choice = select_acquisition_option(
        view=view,
        snapshot=snapshot,
        revealed_fact_ids=frozenset(),
        catalog={target.fact_id: (invasive,)},
        profile=benchmark_patient_profiles()[0],
        policy_id=AcquisitionPolicyId.PATIENT_ADAPTIVE,
    )

    assert choice.selected_option == invasive
    assert choice.action_status is ActionStatus.AWAITING_CLINICIAN_AUTHORIZATION

    cumulative_profile = build_patient_burden_profile(
        "one-visit-limit",
        {"stated_limits": {"max_additional_visits": 1}},
    )
    visit_catalog = {
        item.fact_id: (
            invasive.model_copy(
                update={
                    "option_id": f"{item.fact_id}:visit-fixture",
                    "fact_id": item.fact_id,
                }
            ),
        )
        for item in full_view.available_information
    }
    first = select_acquisition_option(
        view=full_view,
        snapshot=snapshot,
        revealed_fact_ids=frozenset(),
        catalog=visit_catalog,
        profile=cumulative_profile,
        policy_id=AcquisitionPolicyId.PATIENT_ADAPTIVE,
    )
    assert first.selected_option is not None
    second = select_acquisition_option(
        view=full_view,
        snapshot=snapshot,
        revealed_fact_ids=frozenset({first.selected_option.fact_id}),
        catalog=visit_catalog,
        profile=cumulative_profile,
        policy_id=AcquisitionPolicyId.PATIENT_ADAPTIVE,
        selected_options=(first.selected_option,),
    )
    assert second.selected_option is None
    assert any(
        "추가 방문 한도 초과" in item.reason
        for item in second.decision_trace.removed_options
    )


def test_synthetic_runner_records_approval_before_releasing_new_results() -> None:
    spec = load_public_benchmark_spec(CONFIG_PATH)
    group = spec.groups[1]
    base = group.profiles[0]
    mask = group.masks[0]
    case = build_public_case(group, base, mask)
    run = run_burden_policy(
        case=case,
        base_profile_id=base.profile_id,
        split=base.split,
        mask_id=mask.mask_id,
        patient_profile=benchmark_patient_profiles()[0],
        availability=AvailabilityStructure.NEW_CONFIRMATION_NEEDED,
        policy_id=AcquisitionPolicyId.ALL_INFORMATION,
    )

    protected = [
        item
        for item in run.action_history
        if item.decision.action_status
        in {
            ActionStatus.AWAITING_PATIENT_CHOICE,
            ActionStatus.AWAITING_CLINICIAN_AUTHORIZATION,
        }
    ]
    assert protected
    assert all(item.synthetic_authorization_granted for item in protected)
    assert all(item.answer_released for item in protected)
    assert run.metrics.unauthorized_auto_actions == 0


def test_full_benchmark_separates_limit_filtering_from_feasible_ranking(
    tmp_path,
) -> None:
    cache = _fake_source_cache(tmp_path / "source-cache")
    summary_path = run_public_burden_benchmark(
        CONFIG_PATH, cache, tmp_path / "run"
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary["patient_setting_count"] == 360
    assert summary["policy_count"] == 7
    assert summary["policy_run_count"] == 2520
    assert summary["route_choice_run_count"] == 120
    assert summary["model_calls"] == 0
    assert summary["model_tokens"] == 0
    assert summary["source_audit_criterion_count"] == 80
    assert len(summary["policy_metrics"]) == 2 * 7
    route_choice = summary["route_choice_evaluation"]
    assert route_choice["same_final_judgment_masked_case_count"] == 40
    assert route_choice["same_selected_fact_order_masked_case_count"] == 40
    assert route_choice["route_choice_run_count"] == 120
    route_profiles = {
        item["patient_profile_id"]: item
        for item in route_choice["profile_metrics"]
    }
    assert route_profiles["low_extra_burden"]["new_test_total"] == 0
    assert route_profiles["mobility_cost_constrained"]["new_test_total"] == 0
    assert route_profiles["time_urgent"]["new_test_total"] > 0
    mechanisms = summary["mechanism_ablation"]
    hard_filter = mechanisms["disallowed_path_filter"]
    ranking = mechanisms["remaining_feasible_path_ranking"]
    assert hard_filter["base_patient_count"] == 20
    assert len(ranking["comparisons"]) == 3
    assert (
        ranking["effect_identification"]
        == "not_identified_in_current_route_catalog"
    )
    assert all(
        item["comparison_status"] == "not_identified"
        and item["question_order_control"]
        == "patient_limits_only on both sides"
        and item["metric_means"]["pending_trial_count"]["difference"] == 0
        and item["metric_means"][
            "burden_feasible_trial_status_recovery"
        ]["difference"]
        == 0
        for item in ranking["comparisons"]
    )
    assert (
        hard_filter["paired_inference"]["trial_status_recovery"]["cluster_unit"]
        == "base_patient"
    )
    assert hard_filter["metric_means"]["pending_trial_count"]["candidate"] >= 0
    assert hard_filter["metric_means"]["fully_resolved_setting"]["candidate"] >= 0
    assert hard_filter["metric_totals"]["pending_trial_count"]["candidate"] >= 0
    assert (
        hard_filter["paired_inference"]["pending_trial_count"]["cluster_unit"]
        == "base_patient"
    )
    assert set(summary["adoption_comparison"]["gates"]) == {
        "unauthorized_auto_actions_zero",
        "explicit_limit_violations_zero",
        "heldout_new_test_permitted_full_recovery_loss_within_2pp",
        "heldout_constrained_feasible_recovery_loss_within_2pp",
        "constrained_new_test_visit_reduction_at_least_20pct",
        "urgent_delay_not_worse",
        "development_and_heldout_direction_consistent",
    }
    assert len(
        (tmp_path / "run" / "case-results.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
    ) == 2520
