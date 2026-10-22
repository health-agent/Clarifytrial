from datetime import date, datetime, timezone
from itertools import product

import pytest

from clarifytrial.contracts import EvidenceFact, PatientState, TrialCriterion
from clarifytrial.evidence_corroboration import EvidenceOrigin, evaluate_corroborated_criterion
from clarifytrial.interactive.corroboration_planner import CorroborationPlanner
from clarifytrial.interactive.paper_benchmark import PublicAcquisitionModel, ScalarRule, erroneous_value_index, VALUE_GRIDS


AS_OF=datetime(2026,10,23,12,tzinfo=timezone.utc)


def criterion():
    return TrialCriterion(criterion_id="synthetic-c",trial_id="synthetic",kind="inclusion",statement="Synthetic minimum",
        source_location="synthetic://protocol",numeric_constraint={"concept":"egfr","operator":"gte","threshold":30,"unit":"mL/min"},
        evidence_requirement={"max_age_days":14,"allowed_source_types":["medical_record"],"allowed_verification_statuses":["verified"]})


def fact(eid,value=50,**updates):
    return EvidenceFact.model_validate({"evidence_id":eid,"statement":"Synthetic fixed measurement","source_type":"medical_record",
        "source_location":"synthetic://"+eid,"event_date":date(2026,10,22),"verification_status":"verified","concept":"egfr","value":value,"unit":"mL/min",**updates})


def check(facts,groups,scopes=None,minimum=2):
    origins={eid:EvidenceOrigin(group,(scopes or {}).get(eid,"target")) for eid,group in groups.items()}
    return evaluate_corroborated_criterion(criterion(),PatientState(patient_id="synthetic",as_of=AS_OF,facts=facts),
        origins=origins,comparison_scope="target",minimum_source_groups=minimum)


def test_two_declared_sources_agree_with_all_ids_and_no_pending_question():
    r=check([fact("a"),fact("b")],{"a":"lab-A","b":"lab-B"})
    assert r.assessment.evidence_sufficiency.value=="sufficient"
    assert r.assessment.evidence_ids==["a","b"] and r.question is None
    assert r.medical_disclaimer


@pytest.mark.parametrize("groups,scopes",[({"a":"same","b":"same"},None),({"a":"same","b":"same "},None),({"a":"A"},None),
    ({"a":"A","b":"B"},{"b":"different-target"})])
def test_files_unknown_origins_and_different_scopes_are_not_two_sources(groups,scopes):
    r=check([fact("a"),fact("b")],groups,scopes)
    assert r.assessment.evidence_sufficiency.value=="insufficient" and r.question


@pytest.mark.parametrize("update",[{"event_date":date(2026,8,1)},{"event_date":None},
    {"event_date":date(2026,10,24)},{"verification_status":"pending"},{"source_type":"patient_report"},{"unit":"mg/dL"}])
def test_bad_metadata_or_unit_cannot_complete_the_quorum(update):
    r=check([fact("a"),fact("b",**update)],{"a":"A","b":"B"})
    assert r.assessment.evidence_sufficiency.value=="insufficient"


@pytest.mark.parametrize("other",[20,55])
def test_exact_value_disagreement_is_not_hidden_by_equal_criterion_direction(other):
    r=check([fact("a"),fact("b",other)],{"a":"A","b":"B"})
    assert r.assessment.evidence_sufficiency.value=="conflicting" and r.question
    assert r.assessment.evidence_ids==["a","b"]


def test_older_source_is_not_combined_with_the_newest_measurement():
    r=check([fact("a"),fact("b",event_date=date(2026,10,21))],{"a":"A","b":"B"})
    assert r.assessment.evidence_sufficiency.value=="insufficient"


def test_common_wrong_value_can_still_pass_declared_source_agreement():
    # Latent synthetic truth is 50; both sources instead report 20. This guard
    # has no access to that truth and cannot detect perfectly shared errors.
    r=check([fact("a",20),fact("b",20)],{"a":"A","b":"B"})
    assert r.assessment.evidence_sufficiency.value=="sufficient"
    assert r.assessment.clinical_status.value=="violates"


def test_explicit_current_conflict_is_not_overruled_by_older_agreeing_sources():
    r=check([fact("a"),fact("b"),fact("c",20,event_date=date(2026,10,23),verification_status="conflicting")],
        {"a":"A","b":"B","c":"C"})
    assert r.assessment.evidence_sufficiency.value=="conflicting" and "c" in r.assessment.evidence_ids


def test_resolved_or_does_not_ask_for_the_unneeded_corroboration_branch():
    from clarifytrial.interactive.contracts import InteractivePolicyView,InteractiveTrial
    from clarifytrial.interactive.corroboration_runtime import CorroboratedViewResult,evaluate_corroborated_view
    c=criterion()
    d=c.model_copy(update={"criterion_id":"other","numeric_constraint":c.numeric_constraint.model_copy(update={"concept":"other"})})
    view=InteractivePolicyView(case_id="synthetic",disease_group="synthetic",action_budget=3,
        trials=[InteractiveTrial(trial_id="synthetic",criteria=[c,d],eligibility_logic={"operator":"any","children":[
            {"operator":"criterion","criterion_id":"synthetic-c"},{"operator":"criterion","criterion_id":"other"}]})])
    origins={eid:EvidenceOrigin(group,"target") for eid,group in (("a","A"),("b","B"))}
    result=evaluate_corroborated_view(view,PatientState(patient_id="synthetic",as_of=AS_OF,facts=[fact("a"),fact("b")]),
        origins=origins,scope_by_concept={"egfr":"target","other":"other-scope"})
    assert result.decisions[0].confirmation_status.value=="confirmed"
    assert not result.pending_evidence_requests and not result.decisions[0].pending_information
    assert CorroboratedViewResult.model_validate_json(result.model_dump_json())==result


def test_error_channel_changes_binary_and_all_other_grid_values():
    for grid in VALUE_GRIDS.values():
        for index in range(len(grid)):
            assert erroneous_value_index(index,len(grid))!=index
    with pytest.raises(ValueError):erroneous_value_index(0,1)


def test_planner_expected_agreement_matches_complete_noise_enumeration():
    model=PublicAcquisitionModel(("x",),((0.,1.),),((.5,.5),),((ScalarRule(0,"gte",1,False),),))
    planner=CorroborationPlanner(model,quality=(.7,.95),error_probability=.05)
    value,action=planner.solve((-1,),(-1,),0,1,2)
    direct=0.
    for truth,e0,e1 in product((0,1),repeat=3):
        prob=.5*(.05 if e0 else .95)*(.05 if e1 else .95)*.7*.95
        direct+=prob*((truth+e0)%2==(truth+e1)%2)
    assert value[0]==pytest.approx(direct) and action==(0,0)
    assert planner.solve((-1,),(-1,),0,1,1)[1] is None
    copied=CorroborationPlanner(model,distinct_source_groups=False)
    assert copied.solve((-1,),(-1,),0,1,3)[1] is None


@pytest.mark.parametrize("shift",[1,2])
def test_nonuniform_report_posterior_preserves_the_error_channel(shift):
    from clarifytrial.interactive.paper_benchmark import LogicalRule
    prior=(.6,.3,.1)
    model=PublicAcquisitionModel(("x","blocked"),((0.,1.,2.),(0.,1.)),(prior,(.5,.5)),
        ((ScalarRule(0,"gte",2,False),ScalarRule(1,"gte",1,False)),),
        (LogicalRule("any",children=(LogicalRule("criterion",0),LogicalRule("criterion",1))),))
    planner=CorroborationPlanner(model,quality=(.7,.95),error_probability=.05,error_shift=shift)
    value,action=planner.solve((-1,-1),(-1,-1),12,1,2)
    direct=0.
    for true_index in range(3):
        for e0,e1 in product((False,True),repeat=2):
            y0=erroneous_value_index(true_index,3,shift=shift) if e0 else true_index
            y1=erroneous_value_index(true_index,3,shift=shift) if e1 else true_index
            direct+=prior[true_index]*(.05 if e0 else .95)*(.05 if e1 else .95)*.7*.95*(y0==y1==2)
    assert value[0]==pytest.approx(direct) and action==(0,0)
