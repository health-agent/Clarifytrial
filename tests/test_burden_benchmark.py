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
from clarifytrial.contracts import NextAction
from clarifytrial.interactive.oracle import evaluate_interactive_case


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
    assert any(
        "기존 자료 경로 우선" in item.reason
        for item in existing_choice.decision_trace.removed_options
    )
    assert all(
        item.acquisition_mode
        in {
            AcquisitionMode.INTERNAL_RECORD,
            AcquisitionMode.OUTSIDE_RECORD,
            AcquisitionMode.EXISTING_OFFICIAL_RESULT,
        }
        for item in existing_choice.alternative_options
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


def test_full_benchmark_writes_360_settings_and_1800_policy_runs(tmp_path) -> None:
    cache = _fake_source_cache(tmp_path / "source-cache")
    summary_path = run_public_burden_benchmark(
        CONFIG_PATH, cache, tmp_path / "run"
    )
    summary = json.loads(summary_path.read_text(encoding="utf-8"))

    assert summary["patient_setting_count"] == 360
    assert summary["policy_count"] == 5
    assert summary["policy_run_count"] == 1800
    assert summary["model_calls"] == 0
    assert summary["model_tokens"] == 0
    assert summary["source_audit_criterion_count"] == 80
    assert len(summary["policy_metrics"]) == 2 * 5
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
    ) == 1800
