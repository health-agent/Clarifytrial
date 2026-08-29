from pathlib import Path

from clarifytrial.contracts import NextAction
from clarifytrial.interactive import (
    AcquisitionMode,
    AcquisitionPolicyId,
    build_patient_burden_profile,
    build_public_case,
    load_public_benchmark_spec,
    select_acquisition_option,
)
from clarifytrial.interactive.burden_contracts import (
    AcquisitionOption,
    DirectCostBand,
)
from clarifytrial.interactive.oracle import evaluate_interactive_case


CONFIG_PATH = Path("configs/interactive_public_benchmark_v1.json")


def _one_fact_case():
    spec = load_public_benchmark_spec(CONFIG_PATH)
    group = spec.groups[0]
    case = build_public_case(group, group.profiles[0], group.masks[0])
    full_view = case.public_policy_view()
    target = full_view.available_information[0]
    view = full_view.model_copy(update={"available_information": [target]})
    snapshot = evaluate_interactive_case(case, case.initial_patient_state())
    return target.fact_id, view, snapshot


def test_patient_preference_changes_the_route_between_two_feasible_choices() -> None:
    fact_id, view, snapshot = _one_fact_case()
    quick_upload = AcquisitionOption(
        option_id=f"{fact_id}:quick-upload",
        fact_id=fact_id,
        action=NextAction.REQUEST_VERIFICATION,
        acquisition_mode=AcquisitionMode.EXISTING_OFFICIAL_RESULT,
        available_now=True,
        expected_delay_hours=0.5,
        visit_required=False,
        direct_cost_band=DirectCostBand.LOW,
        physical_burden_0_to_3=0,
        emotional_burden_0_to_3=2,
        medical_risk_0_to_3=0,
        treatment_disruption_0_to_3=0,
        source_note="합성 평가용 빠른 기존 결과 제출 경로",
    )
    outside_record = AcquisitionOption(
        option_id=f"{fact_id}:outside-record",
        fact_id=fact_id,
        action=NextAction.LOOKUP_RECORD,
        acquisition_mode=AcquisitionMode.OUTSIDE_RECORD,
        available_now=True,
        expected_delay_hours=48,
        visit_required=False,
        direct_cost_band=DirectCostBand.NONE,
        physical_burden_0_to_3=0,
        emotional_burden_0_to_3=0,
        medical_risk_0_to_3=0,
        treatment_disruption_0_to_3=0,
        source_note="합성 평가용 느린 외부 기록 요청 경로",
    )
    catalog = {fact_id: (quick_upload, outside_record)}

    fastest = build_patient_burden_profile(
        "fastest",
        {
            "time_urgency_0_to_3": 3,
            "preference_mode": "fastest",
            "stated_limits": {"explicitly_no_limits": True},
        },
    )
    least_extra_burden = build_patient_burden_profile(
        "least-extra-burden",
        {
            "cost_sensitivity_0_to_3": 3,
            "preference_mode": "least_extra_burden",
            "stated_limits": {"explicitly_no_limits": True},
        },
    )

    quick_choice = select_acquisition_option(
        view=view,
        snapshot=snapshot,
        revealed_fact_ids=frozenset(),
        catalog=catalog,
        profile=fastest,
        policy_id=AcquisitionPolicyId.PATIENT_ADAPTIVE,
    )
    low_burden_choice = select_acquisition_option(
        view=view,
        snapshot=snapshot,
        revealed_fact_ids=frozenset(),
        catalog=catalog,
        profile=least_extra_burden,
        policy_id=AcquisitionPolicyId.PATIENT_ADAPTIVE,
    )

    assert quick_choice.selected_option == quick_upload
    assert quick_choice.decision_trace.first_decisive_difference == "delay:min=0.5"
    assert low_burden_choice.selected_option == outside_record
    assert low_burden_choice.decision_trace.first_decisive_difference == "cost:min=0"
