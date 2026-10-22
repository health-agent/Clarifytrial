from datetime import date
from itertools import combinations

import pytest

from clarifytrial.interactive.acquisition_evaluation import attainable_ceilings
from clarifytrial.interactive.acquisition_planner import plan_acquisition
from clarifytrial.contracts import NextAction
from clarifytrial.interactive.burden_contracts import AcquisitionMode, AcquisitionOption
from clarifytrial.interactive.burden_policy import build_patient_burden_profile
from clarifytrial.interactive.contracts import ExactPolicyObjective, ScenarioDistribution
from clarifytrial.interactive.exact_policy import (
    ExactDecisionTreePolicy, build_uniform_binary_scenarios, compress_planning_distribution,
    plan_constrained_question_tree,
)
from clarifytrial.interactive.oracle import evaluate_interactive_case, evaluate_policy_view, state_with_revealed_facts
from clarifytrial.interactive.pilot_cases import build_interactive_pilot_cases
from clarifytrial.interactive.public_benchmark import (
    build_public_case, build_public_planning_distribution, load_public_benchmark_spec,
)
from clarifytrial.interactive.stress import build_stress_case


def _plan(case, *, allowed=None, units=None, visit_budget=None):
    view = case.public_policy_view()
    ids = {item.fact_id for item in view.available_information}
    return plan_constrained_question_tree(
        view=view, initial_state=case.initial_patient_state(),
        snapshot=evaluate_interactive_case(case, case.initial_patient_state()),
        distribution=build_uniform_binary_scenarios(case),
        revealed_fact_ids=frozenset(), remaining_budget=3,
        allowed_fact_ids=ids if allowed is None else allowed,
        acquisition_units_by_fact=units or {},
        remaining_acquisition_units=visit_budget,
    )


def _catalog(case):
    modes = {
        NextAction.ASK_PATIENT: AcquisitionMode.PATIENT_REPORT,
        NextAction.LOOKUP_RECORD: AcquisitionMode.INTERNAL_RECORD,
        NextAction.REQUEST_VERIFICATION: AcquisitionMode.EXISTING_OFFICIAL_RESULT,
    }
    return {
        item.fact_id: [AcquisitionOption(
            option_id=f"{item.fact_id}:visit", fact_id=item.fact_id,
            action=item.available_actions[0],
            acquisition_mode=modes[item.available_actions[0]],
            available_now=True, visit_required=True, expected_delay_hours=0,
            source_note="synthetic test path requiring a visit",
        )] for item in case.public_policy_view().available_information
    }


def test_inaccessible_facts_remain_uncertain_in_the_plan():
    case = build_interactive_pilot_cases()[0]
    unrestricted = _plan(case)
    only_one = {case.public_policy_view().available_information[0].fact_id}
    restricted = _plan(case, allowed=only_one)
    assert restricted.target_fact_id in only_one | {None}
    assert restricted.value.average_trial_status_recovery < unrestricted.value.average_trial_status_recovery
    assert restricted.belief_scenario_count == unrestricted.belief_scenario_count == 32
    empty = _plan(case, allowed=set())
    assert empty.target_fact_id is None
    assert empty.value.action_count == 0


def test_total_visit_limit_is_enforced_through_the_whole_question_tree():
    case = build_stress_case("fully_separated", 0, seed=20261022)
    costs = {item.request.fact_id: 1 for item in case.hidden_facts}
    choice = _plan(case, units=costs, visit_budget=1)
    assert choice.value.action_count <= 1
    assert choice.value.average_trial_status_recovery == pytest.approx(0.2)
    assert _plan(case, units=costs, visit_budget=0).target_fact_id is None


def test_scenario_reduction_preserves_expected_values_and_probability_mass():
    case = build_interactive_pilot_cases()[0]
    original = build_uniform_binary_scenarios(case)
    expanded = ScenarioDistribution(case_id=case.case_id, scenarios=[
        scenario.model_copy(update={
            "scenario_id": f"{scenario.scenario_id}-copy-{copy}",
            "probability": scenario.probability / 2,
        }) for scenario in original.scenarios for copy in range(2)
    ])
    reduced = compress_planning_distribution(
        case.public_policy_view(), case.initial_patient_state(), expanded,
    )
    assert len(reduced.scenarios) == len(original.scenarios) == 32
    assert sum(item.probability for item in reduced.scenarios) == pytest.approx(1)
    assert [item.probability for item in reduced.scenarios] == [item.probability for item in original.scenarios]
    view = case.public_policy_view()
    snapshot = evaluate_interactive_case(case, case.initial_patient_state())
    raw_policy = ExactDecisionTreePolicy(view, case.initial_patient_state(), expanded, ExactPolicyObjective.EXPECTED)
    reduced_policy = ExactDecisionTreePolicy(view, case.initial_patient_state(), reduced, ExactPolicyObjective.EXPECTED)
    assert raw_policy.select(view, snapshot, frozenset()) == reduced_policy.select(view, snapshot, frozenset())
    assert raw_policy.choices[0].value == reduced_policy.choices[0].value


def test_ceiling_separates_visit_limits_from_action_budget():
    case = build_stress_case("fully_separated", 0, seed=20261022)
    ids = [item.request.fact_id for item in case.hidden_facts]
    snapshots = {
        frozenset(subset): evaluate_interactive_case(
            case, state_with_revealed_facts(case, frozenset(subset)),
        ) for size in range(6) for subset in combinations(ids, size)
    }
    profile = build_patient_burden_profile("two-visits", {
        "stated_limits": {"max_additional_visits": 2},
    })
    ceilings = attainable_ceilings(
        case=case, snapshots=snapshots, catalog=_catalog(case),
        profile=profile, action_budget=1,
    )
    assert ceilings["all_actions_feasible_ceiling"] == pytest.approx(0.4)
    assert ceilings["budget_feasible_ceiling"] == pytest.approx(0.2)
    assert ceilings["constraint_loss"] == pytest.approx(0.6)
    assert ceilings["budget_loss"] == pytest.approx(0.2)


def test_failed_attempts_spend_action_budget_even_without_revealed_answers():
    case = build_stress_case("fully_separated", 0, seed=20261022)
    catalog = _catalog(case)
    option = next(iter(catalog.values()))[0]
    result = plan_acquisition(
        view=case.public_policy_view(), initial_state=case.initial_patient_state(),
        snapshot=evaluate_interactive_case(case, case.initial_patient_state()),
        distribution=build_uniform_binary_scenarios(case),
        catalog=catalog, profile=build_patient_burden_profile("neutral"),
        revealed_fact_ids=frozenset(), selected_options=[option, option, option],
    )
    assert result.decision.selected_option is None
    assert result.planning is None


def test_development_distribution_does_not_read_heldout_hidden_values():
    spec = load_public_benchmark_spec("configs/interactive_public_benchmark_v1.json")
    group = spec.groups[0]
    profile = next(item for item in group.profiles if item.split == "heldout")
    mask = group.masks[0]
    values = dict(profile.values)
    for fact in group.facts:
        if fact.code not in mask.visible_facts:
            values[fact.code] = next(value for value in fact.values if value != values[fact.code])
    changed = profile.model_copy(update={"values": values})
    original_case = build_public_case(group, profile, mask)
    changed_case = build_public_case(group, changed, mask)
    assert original_case.public_policy_view() == changed_case.public_policy_view()
    assert original_case.initial_patient_state() == changed_case.initial_patient_state()
    original = build_public_planning_distribution(group, original_case, profile, mask)
    other = build_public_planning_distribution(group, changed_case, changed, mask)
    assert original == other


def test_selected_route_obeys_the_visit_charge_used_for_planning():
    case = build_stress_case("fully_separated", 0, seed=20261022)
    catalog = _catalog(case)
    for options in catalog.values():
        options.append(options[0].model_copy(update={
            "option_id": options[0].option_id + "-no-visit", "visit_required": False,
        }))
    profile = build_patient_burden_profile("one-visit", {
        "stated_limits": {"max_additional_visits": 1},
    })
    result = plan_acquisition(
        view=case.public_policy_view(), initial_state=case.initial_patient_state(),
        snapshot=evaluate_interactive_case(case, case.initial_patient_state()),
        distribution=build_uniform_binary_scenarios(case), catalog=catalog,
        profile=profile, revealed_fact_ids=frozenset(),
    )
    assert result.decision.selected_option is not None
    assert result.decision.selected_option.visit_required is False


def test_unknown_visit_cost_is_not_counted_as_a_free_visit():
    case = build_stress_case("fully_separated", 0, seed=20261022)
    catalog = _catalog(case)
    for options in catalog.values():
        options[0] = options[0].model_copy(update={"visit_required": None})
    result = plan_acquisition(
        view=case.public_policy_view(), initial_state=case.initial_patient_state(),
        snapshot=evaluate_interactive_case(case, case.initial_patient_state()),
        distribution=build_uniform_binary_scenarios(case), catalog=catalog,
        profile=build_patient_burden_profile("one-visit", {
            "stated_limits": {"max_additional_visits": 1},
        }), revealed_fact_ids=frozenset(),
    )
    assert result.decision.selected_option is None


def test_observations_use_current_evidence_independently_of_record_order():
    case = build_interactive_pilot_cases()[0]
    hidden = case.hidden_facts[0]
    answer = hidden.answer.evidence
    old = answer.model_copy(update={
        "evidence_id": "old-provisional", "event_date": date(2000, 1, 1),
        "recorded_date": date(2000, 1, 1), "value": 999,
    })
    initial = case.initial_patient_state().model_copy(update={
        "facts": [*case.initial_patient_state().facts, old],
    })
    view = case.public_policy_view()
    distribution = build_uniform_binary_scenarios(case)
    choices = []
    for evidence in ([*initial.facts, answer], [*case.initial_patient_state().facts, answer, old]):
        state = initial.model_copy(update={"facts": evidence})
        choices.append(plan_constrained_question_tree(
            view=view, initial_state=initial, snapshot=evaluate_policy_view(view, state),
            distribution=distribution, revealed_fact_ids=frozenset({hidden.request.fact_id}),
            remaining_budget=2, allowed_fact_ids={item.fact_id for item in view.available_information},
            acquisition_units_by_fact={},
        ))
    assert choices[0] == choices[1]
