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


def check(facts,groups,scopes=None,minimum=2,mode="exact_value"):
    origins={eid:EvidenceOrigin(group,(scopes or {}).get(eid,"target")) for eid,group in groups.items()}
    return evaluate_corroborated_criterion(criterion(),PatientState(patient_id="synthetic",as_of=AS_OF,facts=facts),
        origins=origins,comparison_scope="target",minimum_source_groups=minimum,agreement_mode=mode)


def test_two_declared_sources_agree_with_all_ids_and_no_pending_question():
    r=check([fact("a"),fact("b")],{"a":"lab-A","b":"lab-B"})
    assert r.assessment.evidence_sufficiency.value=="sufficient"
    assert r.assessment.evidence_ids==["a","b"] and r.question is None
    assert r.medical_disclaimer


@pytest.mark.parametrize("groups,scopes",[({"a":"same","b":"same"},None),({"a":"same","b":"same "},None),({"a":"A"},None),
    ({"a":"A","b":"B"},{"b":"different-target"})])
@pytest.mark.parametrize("mode",["exact_value","criterion_direction"])
def test_files_unknown_origins_and_different_scopes_are_not_two_sources(groups,scopes,mode):
    r=check([fact("a"),fact("b")],groups,scopes,mode=mode)
    assert r.assessment.evidence_sufficiency.value=="insufficient" and r.question


@pytest.mark.parametrize("update",[{"event_date":date(2026,8,1)},{"event_date":None},
    {"event_date":date(2026,10,24)},{"verification_status":"pending"},{"source_type":"patient_report"},{"unit":"mg/dL"}])
@pytest.mark.parametrize("mode",["exact_value","criterion_direction"])
def test_bad_metadata_or_unit_cannot_complete_the_quorum(update,mode):
    r=check([fact("a"),fact("b",**update)],{"a":"A","b":"B"},mode=mode)
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


def test_direction_agreement_preserves_distinct_values_and_can_accept_two_different_errors():
    r=check([fact("a",50),fact("b",55)],{"a":"A","b":"B"},mode="criterion_direction")
    assert r.assessment.clinical_status.value=="supports" and r.question is None
    assert "values_differ_but_criterion_direction_agrees" in r.issues
    # With latent truth 50, two different erroneous reports can both fail >=30.
    # Direction agreement accepts this wrong predicate; exact agreement withholds.
    bad=[fact("a",20),fact("b",25)]
    assert check(bad,{"a":"A","b":"B"},mode="criterion_direction").assessment.clinical_status.value=="violates"
    assert check(bad,{"a":"A","b":"B"}).assessment.evidence_sufficiency.value=="conflicting"
    assert check([fact("a",20),fact("b",50)],{"a":"A","b":"B"},mode="criterion_direction").assessment.clinical_status.value=="unknown"


def test_direction_for_exclusion_and_current_explicit_conflict():
    c=TrialCriterion.model_validate({**criterion().model_dump(),"kind":"exclusion"})
    state=PatientState(patient_id="synthetic",as_of=AS_OF,facts=[fact("a",50),fact("b",55)])
    origins={eid:EvidenceOrigin(group,"target") for eid,group in (("a","A"),("b","B"),("c","C"))}
    r=evaluate_corroborated_criterion(c,state,origins=origins,comparison_scope="target",agreement_mode="criterion_direction")
    assert r.assessment.clinical_status.value=="violates"
    state=state.model_copy(update={"facts":state.facts+[fact("c",20,event_date=date(2026,10,23),verification_status="conflicting")]})
    r=evaluate_corroborated_criterion(c,state,origins=origins,comparison_scope="target",agreement_mode="criterion_direction")
    assert r.assessment.evidence_sufficiency.value=="conflicting" and "c" in r.assessment.evidence_ids


def test_one_report_pair_can_resolve_one_trial_but_leave_another_predicate_pending():
    from clarifytrial.interactive.contracts import InteractivePolicyView,InteractiveTrial
    from clarifytrial.interactive.corroboration_runtime import evaluate_corroborated_view
    c=criterion()
    d=TrialCriterion.model_validate({**c.model_dump(),"criterion_id":"higher-minimum","trial_id":"other-trial",
        "numeric_constraint":{**c.numeric_constraint.model_dump(),"threshold":52}})
    view=InteractivePolicyView(case_id="synthetic",disease_group="synthetic",action_budget=3,
        trials=[InteractiveTrial(trial_id="synthetic",criteria=[c]),InteractiveTrial(trial_id="other-trial",criteria=[d])])
    result=evaluate_corroborated_view(view,PatientState(patient_id="synthetic",as_of=AS_OF,facts=[fact("a",50),fact("b",55)]),
        origins={eid:EvidenceOrigin(group,"target") for eid,group in (("a","A"),("b","B"))},
        scope_by_concept={"egfr":"target"},agreement_mode="criterion_direction")
    assert [d.confirmation_status.value for d in result.decisions]==["confirmed","uncertain"]
    assert result.criteria["synthetic-c"].assessment.evidence_ids==["a","b"]
    assert result.pending_evidence_requests[0].related_criterion_ids==["higher-minimum"]
    assert not result.full_protocol_confirmation_allowed and result.medical_disclaimer


@pytest.mark.parametrize("mode",["exact_value","criterion_direction"])
@pytest.mark.parametrize("channel",["cyclic","uniform_wrong"])
def test_pair_planner_matches_complete_latent_report_enumeration(mode,channel):
    from clarifytrial.interactive.direction_corroboration import SourcePairModel,SourcePairPlanner,report_channel
    prior=(.6,.3,.1); p=.15
    base=PublicAcquisitionModel(("x",),((0.,1.,2.),),(prior,),
        ((ScalarRule(0,"gte",1,False),),(ScalarRule(0,"gte",2,True),)))
    model=SourcePairModel(base,mode=mode)
    planner=SourcePairPlanner(model,error_probability=p,channel=channel)
    direct=0.
    for t,y,z in product(range(3),repeat=3):
        def likelihood(report):
            return 1-p if report==t else p/2 if channel=="uniform_wrong" else p if report==(t+2)%3 else 0.
        if mode=="exact_value" and y!=z:continue
        resolved=int((y>=1)==(z>=1))+int((y<2)==(z<2))
        direct+=prior[t]*likelihood(y)*likelihood(z)*.7*.95*resolved
    value,action=planner.solve((-1,),(-1,),0,1,2)
    assert value[0]==pytest.approx(direct) and action==(0,0)
    assert planner.solve((-1,),(-1,),0,1,1)[1] is None
    for row in report_channel(3,p,kind=channel):assert sum(row)==pytest.approx(1.)
    # The same pair resolves >=1, but leaves the >=2 predicate unresolved.
    pair=model.encode(0,1,2)
    assert model.decode(0,pair)==(1,2)
    assert model.statuses((pair,))==((1,-1) if mode=="criterion_direction" else (-1,-1))


@pytest.mark.parametrize("prior",[(.6,.3,.1),(.2,.2,.6)])
def test_new_exact_pair_channel_retains_legacy_cyclic_policy(prior):
    from clarifytrial.interactive.direction_corroboration import SourcePairModel,SourcePairPlanner
    from clarifytrial.interactive.paper_benchmark import LogicalRule
    base=PublicAcquisitionModel(("x","blocked"),((0.,1.,2.),(0.,1.)),(prior,(.5,.5)),
        ((ScalarRule(0,"gte",2,False),ScalarRule(1,"gte",1,False)),),
        (LogicalRule("any",children=(LogicalRule("criterion",0),LogicalRule("criterion",1))),))
    old=CorroborationPlanner(base)
    new=SourcePairPlanner(SourcePairModel(base,mode="exact_value"),channel="cyclic")
    assert new.solve((-1,-1),(-1,-1),12,1,3)==old.solve((-1,-1),(-1,-1),12,1,3)
