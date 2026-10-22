import json
from pathlib import Path

import pytest

from clarifytrial.datasets.source_expression import validate_declared_numeric_expression
from clarifytrial.interactive.paper_benchmark import (
    LogicalRule, PublicAcquisitionModel, RoutePlanner, ScalarRule, evidence, make_model,
    reference_statuses, runtime_statuses, runtime_view,
)

ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data/paper_source_benchmark_v2/protocol_subsets.json"


def single_trial(tid):
    data=json.loads(DATA.read_text(encoding="utf-8"))
    group=next(g for g in data["groups"] if any(t["nct_id"]==tid for t in g["trials"]))
    return dict(group,trials=[t for t in group["trials"] if t["nct_id"]==tid])


def evaluate(group, values):
    trial=group["trials"][0]
    units={r["fact_code"]:r["unit"] for r in trial["criteria"]}
    facts=[evidence(code,v,units[code],sequence=i) for i,(code,v) in enumerate(values.items())]
    actual=runtime_statuses(runtime_view(group,trial["split"]),facts)
    assert actual==reference_statuses([trial],values,set(values))
    return actual[0]


@pytest.mark.parametrize("uacr,egfr,expected",[(500,65,1),(50,48,1),(10,35,1),(10,65,0),(500,12,0)])
def test_real_three_alternative_renal_clause_keeps_all_branches(uacr,egfr,expected):
    group=single_trial("NCT06906640")
    assert evaluate(group,{"uacr_mg_g":uacr,"egfr_ml_min_1_73m2":egfr})==expected


@pytest.mark.parametrize("value,liver,expected",[(2,None,1),(4,None,-1),(4,1,1),(4,0,0),(5,1,0)])
def test_liver_exception_respects_both_branch_and_strict_boundary(value,liver,expected):
    group=single_trial("NCT04305834")
    values={"hemoglobin_g_dl":12,"platelet_count_10_9_l":200,"absolute_neutrophil_count_10_9_l":3,
            "creatinine_clearance_ml_min":65,"ast_xuln":value,"alt_xuln":value}
    if liver is not None:values["liver_metastases"]=liver
    assert evaluate(group,values)==expected


@pytest.mark.parametrize("lvef,ntpro,expected",[(35,400,0),(35,600,1),(40,400,0),(50,400,1),(50,100,0)])
def test_heart_marker_threshold_depends_on_ejection_fraction(lvef,ntpro,expected):
    group=single_trial("NCT07214376")
    assert evaluate(group,{"body_mass_index_kg_m2":32,"egfr_ml_min_1_73m2":65,"dialysis":0,
                           "lvef_percent":lvef,"ntprobnp_pg_ml":ntpro})==expected


def test_exclusion_of_an_interval_uses_de_morgan_not_a_flat_and():
    # Exclude patients in [1,2]. Outside either end is compatible. The eligibility
    # tree is OR over the two exclusion-facing scalar supports.
    rules=((ScalarRule(0,"gte",1,True),ScalarRule(0,"lte",2,True)),)
    tree=LogicalRule("any",children=(LogicalRule("criterion",0),LogicalRule("criterion",1)))
    model=PublicAcquisitionModel(("x",),((0.,1.,2.,3.),),((.25,)*4,),rules,(tree,))
    assert [model.statuses((i,))[0] for i in range(4)]==[1,0,0,1]


def test_question_menu_ignores_an_already_satisfied_alternative():
    # (a OR b) AND c; if a is true, asking b cannot resolve anything.
    rules=((ScalarRule(0,"eq",1,False),ScalarRule(1,"eq",1,False),ScalarRule(2,"eq",1,False)),)
    tree=LogicalRule("all",children=(LogicalRule("any",children=(LogicalRule("criterion",0),LogicalRule("criterion",1))),LogicalRule("criterion",2)))
    model=PublicAcquisitionModel(("a","b","c"),((0.,1.),)*3,((.5,.5),)*3,rules,(tree,))
    assert model.information_needs((1,-1,-1))==({2},)


def test_literal_guard_rejects_sign_flip_and_count_scale_errors():
    with pytest.raises(ValueError,match="not supported"):
        validate_declared_numeric_expression({"fact_code":"hba1c","operator":"gt","threshold":8.5,"unit":"%"},"HbA1c <8.5%")
    with pytest.raises(ValueError,match="conversion"):
        validate_declared_numeric_expression({"fact_code":"platelet_count_10_9_l","operator":"gte","threshold":80000.,"unit":"10^9/L", "source_threshold":80000.,"source_unit":"/mm3"},"Platelets >=80,000/mm3")
    with pytest.raises(ValueError,match="unwritten"):
        validate_declared_numeric_expression({"fact_code":"ecog_score","operator":"eq","threshold":3,"unit":"score"},"ECOG 0 or 1")


def test_all_new_logic_agrees_with_runtime_over_partial_generated_states():
    data=json.loads(DATA.read_text(encoding="utf-8"))
    for group in data["groups"]:
        model=make_model(group,"heldout")
        trials=[t for t in group["trials"] if t["split"]=="heldout"]
        view=runtime_view(group,"heldout")
        units={r["fact_code"]:r["unit"] for t in group["trials"] for r in t["criteria"]}
        # Use four globally varied values and every single-fact withholding;
        # branch-specific tests above provide non-tautological expected labels.
        for quantile in (0.,.33,.66,1.):
            indices=tuple(int(quantile*(len(d)-1)) for d in model.domains)
            for hidden in range(-1,len(model.codes)):
                observed=tuple(-1 if j==hidden else v for j,v in enumerate(indices))
                values={code:model.domains[j][indices[j]] for j,code in enumerate(model.codes)}
                usable={code for j,code in enumerate(model.codes) if observed[j]>=0}
                facts=[evidence(code,values[code],units[code],sequence=j) for j,code in enumerate(model.codes) if code in usable]
                assert model.statuses(observed)==reference_statuses(trials,values,usable)==runtime_statuses(view,facts)


def test_value_class_compaction_preserves_fallible_route_bellman_values():
    rules=((ScalarRule(0,"gte",2,False),ScalarRule(1,"lt",3,False)),
           (ScalarRule(0,"lt",4,True),))
    model=PublicAcquisitionModel(("a","b"),((0.,1.,2.,3.,4.,5.),)*2,
        ((.1,.1,.1,.2,.2,.3),(.3,.2,.2,.1,.1,.1)),rules)
    raw=RoutePlanner(model,quality=(.7,.95))
    compact=RoutePlanner(model,quality=(.7,.95),compact=True)
    assert len(compact.outcomes[0])<len(raw.outcomes[0])
    for observed in ((-1,-1),(0,-1),(2,-1),(5,-1),(-1,4)):
        for depth in (1,2,3):
            for visits in (0,1):
                expected,_=raw.solve(observed,0,visits,depth)
                actual,_=compact.solve(observed,0,visits,depth)
                assert actual==pytest.approx(expected,abs=1e-9)


def test_source_boolean_guard_does_not_accept_unquoted_or_nonbinary_values():
    with pytest.raises(ValueError,match="absent"):
        validate_declared_numeric_expression({"fact_code":"dialysis","operator":"eq","threshold":1,"unit":"bool"},"eGFR <30")
    with pytest.raises(ValueError,match="vocabulary"):
        validate_declared_numeric_expression({"fact_code":"liver_metastases","operator":"eq","threshold":2,"unit":"bool"},"liver metastases")
