import json
from pathlib import Path

import pytest

from clarifytrial.interactive.paper_benchmark import (
    PublicAcquisitionModel, RoutePlanner, ScalarRule, evidence, make_model,
    reference_statuses, runtime_statuses, runtime_view,
)
from clarifytrial.datasets.protocol_scope import describe_protocol_scope


DATA = Path(__file__).resolve().parents[1] / "data/paper_source_benchmark_v1/protocol_subsets.json"


def toy():
    return PublicAcquisitionModel(("a","b"), ((0.,1.),(0.,1.)), ((.5,.5),(.5,.5)),
        ((ScalarRule(0,"gte",1,False),ScalarRule(1,"gte",1,False)),))


def test_future_complementarity_can_justify_an_action():
    # No single query confirms an all-pass trial; two queries can. Exclusions
    # also contribute, so the two-step value is strictly above one-step value.
    planner=RoutePlanner(toy(),quality=(1.,1.))
    one,_=planner.solve((-1,-1),0,0,1)
    two,action=planner.solve((-1,-1),0,0,2)
    assert one[0] == .5
    assert two[0] == 1
    assert action in {(0,0),(1,0)}


def test_fallible_routes_consume_budget_but_leave_alternative_available():
    planner=RoutePlanner(toy(),quality=(0.,1.))
    _,action=planner.solve((-1,-1),0,1,1)
    assert action[1] == 1
    _,fallback=planner.solve((-1,-1),1,1,1)
    assert fallback == (0,1)
    assert planner.solve((-1,-1),0,0,3)[1] is None


def test_total_visit_limit_covers_future_branches():
    planner=RoutePlanner(toy(),quality=(0.,1.))
    value,_=planner.solve((-1,-1),0,1,3)
    assert value[0] == .5
    assert -value[1] <= 1
    with pytest.raises(ValueError):
        planner.solve((-1,-1),0,1,4)


def test_visit_penalty_can_prefer_leaving_a_trial_unresolved():
    planner=RoutePlanner(toy(),quality=(0.,1.),visit_penalty=1.)
    assert planner.solve((-1,-1),0,1,3)[1] is None


def test_protocol_split_and_old_trials_are_disjoint():
    data=json.loads(DATA.read_text(encoding="utf-8"))
    dev={t["nct_id"] for g in data["groups"] for t in g["trials"] if t["split"]=="development"}
    held={t["nct_id"] for g in data["groups"] for t in g["trials"] if t["split"]=="heldout"}
    assert len(dev)==len(held)==24
    assert not dev & held
    assert not (dev|held) & set(data["prior_trial_ids"])


@pytest.mark.parametrize("outcome",["valid","no_answer","stale","missing_date","wrong_source","unverified"])
def test_independent_reference_agrees_with_runtime_on_evidence_quality(outcome):
    group=json.loads(DATA.read_text(encoding="utf-8"))["groups"][0]
    view=runtime_view(group,"heldout")
    model=make_model(group,"heldout")
    trials=[t for t in group["trials"] if t["split"]=="heldout"]
    values={code:model.domains[j][0] for j,code in enumerate(model.codes)}
    units={r["fact_code"]:r["unit"] for t in group["trials"] for r in t["criteria"]}
    facts=[evidence(code,values[code],units[code],outcome,j) for j,code in enumerate(model.codes)]
    expected=reference_statuses(trials,values,set(values) if outcome=="valid" else set())
    assert runtime_statuses(view,facts)==expected


def test_bad_return_does_not_override_sufficient_evidence_in_either_order():
    group=json.loads(DATA.read_text(encoding="utf-8"))["groups"][0]
    view=runtime_view(group,"heldout")
    units={r["fact_code"]:r["unit"] for t in group["trials"] for r in t["criteria"]}
    code=next(iter(units))
    good=evidence(code,25,units[code],"valid",1)
    bad=evidence(code,100,units[code],"unverified",2)
    expected=runtime_statuses(view,[good])
    assert runtime_statuses(view,[good,bad])==runtime_statuses(view,[bad,good])==expected


def test_source_threshold_boundaries_outside_the_patient_grid():
    # Check the exact comparator boundary and both sides, not only the fixed
    # synthetic sample values used for policy evaluation.
    data=json.loads(DATA.read_text(encoding="utf-8"))
    for group in data["groups"]:
        for split in ("development","heldout"):
            view=runtime_view(group,split)
            trials=[t for t in group["trials"] if t["split"]==split]
            criteria=[r for t in trials for r in t["criteria"]]
            for row in criteria:
                for delta in (-.01,0,.01):
                    values={r["fact_code"]:r["threshold"] for r in criteria}
                    values[row["fact_code"]]=row["threshold"]+delta
                    units={r["fact_code"]:r["unit"] for r in criteria}
                    facts=[evidence(code,value,units[code],sequence=j) for j,(code,value) in enumerate(values.items())]
                    assert runtime_statuses(view,facts)==reference_statuses(trials,values,set(values))


def test_verified_metadata_cannot_establish_the_truth_of_a_numeric_value():
    # A falsified but fresh/verified value passes the metadata guard. This
    # counterexample bounds the safety claim; it is not an expected fix target.
    row={"criterion_id":"synthetic-bmi", "fact_code":"body_mass_index_kg_m2", "operator":"gte",
         "threshold":30., "kind":"inclusion", "unit":"kg/m2", "source_text":"Synthetic BMI >=30",
         "source_location":"synthetic://criterion"}
    trial={"nct_id":"synthetic-trial","split":"heldout","criteria":[row]}
    view=runtime_view({"group_id":"synthetic","trials":[trial]},"heldout")
    wrong_but_verified=evidence("body_mass_index_kg_m2",35.,"kg/m2")
    assert runtime_statuses(view,[wrong_but_verified])==(1,)
    assert reference_statuses([trial],{"body_mass_index_kg_m2":20.},{"body_mass_index_kg_m2"})==(0,)


def test_subset_scope_keeps_every_unrepresented_line_and_never_certifies_protocol():
    data=json.loads(DATA.read_text(encoding="utf-8"))
    for group in data["groups"]:
        for trial in group["trials"]:
            scope=describe_protocol_scope(trial)
            source={i for i,line in enumerate(trial["eligibility_text"].splitlines(),1) if line.strip()}
            represented={r["line_number"] for r in trial["criteria"]}
            assert set(scope["unrepresented_source_line_numbers"])==source-represented
            assert scope["full_protocol_confirmation_allowed"] is False
    damaged=dict(trial, criteria=[dict(trial["criteria"][0],source_text="invented source")])
    with pytest.raises(ValueError,match="source span"):
        describe_protocol_scope(damaged)
