from datetime import date,datetime,timezone
from itertools import product

import pytest
from pydantic import ValidationError

from clarifytrial.contracts import CriterionAssessment,EvidenceFact,PatientState
from clarifytrial.llm import ScriptedStructuredModel
from clarifytrial.mechanical_checks import evaluate_criterion
from clarifytrial.preparation.contracts import TrialProtocolDraft,TrialProtocolSource
from clarifytrial.preparation.trial_protocol import TrialProtocolStructurerAgent,structure_trial_protocol
from clarifytrial.trace import TraceRecorder
from clarifytrial.workflow.patient_screening_rules import aggregate_screening_trial


ATOMS=[("eGFR >=30 mL/min/1.73m2","egfr","gte",30,"mL/min/1.73m2"),
       ("HbA1c <7%","hba1c","lt",7,"%"),
       ("Age >=18 years","age","gte",18,"years")]


def drafts(kind,atoms=ATOMS[:2]):
    return [{"criterion_key":f"p{i}","kind":kind,"statement":s,"source_quote":s,
        "numeric_constraint":{"concept":c,"operator":op,"threshold":v,"unit":u}}
        for i,(s,c,op,v,u) in enumerate(atoms)]


def prepare(text,items,groups=(),*,chunk_limit=8000):
    model=ScriptedStructuredModel({"trial_protocol_structurer":lambda _: {"criteria":items,"logic_groups":groups}})
    trace=TraceRecorder("synthetic-source-logic")
    result=structure_trial_protocol(TrialProtocolSource(trial_id="synthetic-logic",title="Synthetic logic source",
        eligibility_text=text,source_location="synthetic://logic"),TrialProtocolStructurerAgent(model),
        trace=trace,chunk_char_limit=chunk_limit)
    return result,trace


def decide(prepared,values):
    units={"egfr":"mL/min/1.73m2","hba1c":"%","age":"years","ecog":"score"}
    state=PatientState(patient_id="synthetic",as_of=datetime(2026,10,22,12,tzinfo=timezone.utc),
        facts=[EvidenceFact(evidence_id=c,statement="Synthetic "+c,source_type="medical_record",
            source_location="synthetic://patient",verification_status="verified",event_date=date(2026,10,22),
            concept=c,value=v,unit=units[c]) for c,v in values.items()])
    assessments={}
    for criterion in prepared.trial.criteria:
        checked=evaluate_criterion(criterion,state)
        assessments[criterion.criterion_id]=CriterionAssessment(criterion_id=criterion.criterion_id,
            criterion_source_location=criterion.source_location,clinical_status=checked.clinical_status,
            evidence_sufficiency=checked.evidence_sufficiency,evidence_ids=checked.evidence_ids,rationale="Deterministic synthetic check.")
    return aggregate_screening_trial(trial=prepared.trial,assessments=assessments,evidence_requests=[],patient_state=state)


@pytest.mark.parametrize("kind,connector",product(("inclusion","exclusion"),("and","or")))
def test_source_connective_and_exclusion_negation_survive_natural_preparation(kind,connector):
    text=kind.title()+" Criteria:\n"+f" {connector} ".join(a[0] for a in ATOMS[:2])
    prepared,_=prepare(text,drafts(kind))
    assert prepared.trial.protocol_logic_supported
    assert prepared.trial.eligibility_logic is not None
    for left,right in product((False,True),repeat=2):
        membership=(left and right) if connector=="and" else (left or right)
        expected=membership if kind=="inclusion" else not membership
        decision=decide(prepared,{"egfr":50 if left else 20,"hba1c":6 if right else 8})
        assert decision.confirmation_status.value==("confirmed" if expected else "ineligible")


@pytest.mark.parametrize("kind,connector,known,expected",[
    ("inclusion","or",{"hba1c":6},"confirmed"),
    ("inclusion","or",{"hba1c":8},"not_confirmed"),
    ("exclusion","and",{"hba1c":8},"confirmed"),
    ("exclusion","and",{"hba1c":6},"not_confirmed"),
])
def test_unknown_atom_is_not_silently_false_or_required_in_a_resolved_branch(kind,connector,known,expected):
    text=kind.title()+" Criteria:\n"+f" {connector} ".join(a[0] for a in ATOMS[:2])
    prepared,_=prepare(text,drafts(kind))
    assert decide(prepared,known).confirmation_status.value==expected


def test_wrong_model_relation_is_corrected_from_the_source_not_trusted():
    quote=" or ".join(a[0] for a in ATOMS[:2])
    group={"source_quote":quote,"predicate_logic":{"operator":"all","children":[
        {"operator":"criterion","criterion_id":"p0"},{"operator":"criterion","criterion_id":"p1"}]}}
    prepared,trace=prepare("Inclusion Criteria:\n"+quote,drafts("inclusion"),[group])
    assert prepared.trial.protocol_logic_supported
    assert decide(prepared,{"egfr":50,"hba1c":8}).confirmation_status.value=="confirmed"
    event=next(e for e in trace.events if e.event=="trial_protocol_structured")
    assert event.output["source_logic_groups"][0]["model_relation_corrected"]


@pytest.mark.parametrize("kind",["inclusion","exclusion"])
def test_two_of_three_source_list_keeps_threshold_under_negation(kind):
    quote="At least two of the following must apply:\n"+"\n".join("* "+a[0] for a in ATOMS)
    group={"source_quote":quote,"predicate_logic":{"operator":"at_least","minimum_required":2,
        "children":[{"operator":"criterion","criterion_id":f"p{i}"} for i in range(3)]}}
    prepared,_=prepare(kind.title()+" Criteria:\n"+quote,drafts(kind,ATOMS),[group])
    assert prepared.trial.protocol_logic_supported,prepared.trial.protocol_logic_issues
    for bits in product((False,True),repeat=3):
        membership=sum(bits)>=2
        expected=membership if kind=="inclusion" else not membership
        values={"egfr":50 if bits[0] else 20,"hba1c":6 if bits[1] else 8,"age":25 if bits[2] else 10}
        assert decide(prepared,values).confirmation_status.value==("confirmed" if expected else "ineligible")


def test_shared_numeric_range_can_be_split_without_losing_its_upper_bound():
    quote="eGFR >=30 and <60 mL/min/1.73m2"
    items=drafts("inclusion",[(quote,"egfr","gte",30,"mL/min/1.73m2"),(quote,"egfr","lt",60,"mL/min/1.73m2")])
    prepared,_=prepare("Inclusion Criteria:\n"+quote,items)
    assert prepared.trial.protocol_logic_supported,prepared.trial.protocol_logic_issues
    for value,expected in ((20,"ineligible"),(30,"confirmed"),(59,"confirmed"),(60,"ineligible")):
        assert decide(prepared,{"egfr":value}).confirmation_status.value==expected


def test_ambiguous_mixed_connectors_and_unbound_if_remain_unconfirmed():
    for text,items in [
        ("Inclusion Criteria:\n"+ATOMS[0][0]+" or "+ATOMS[1][0]+" and "+ATOMS[2][0],drafts("inclusion",ATOMS)),
        ("Inclusion Criteria:\nIf liver metastases are present, "+ATOMS[0][0],drafts("inclusion",ATOMS[:1])),
    ]:
        prepared,_=prepare(text,items)
        assert not prepared.trial.protocol_logic_supported
        assert decide(prepared,{"egfr":50,"hba1c":6,"age":25}).confirmation_status.value=="not_confirmed"


def test_unknown_keys_and_shared_keys_are_rejected_at_the_draft_boundary():
    base={"criteria":drafts("inclusion")}
    with pytest.raises(ValidationError,match="unknown criterion_key"):
        TrialProtocolDraft.model_validate({**base,"logic_groups":[{"source_quote":"source","predicate_logic":{"operator":"criterion","criterion_id":"invented"}}]})
    leaf={"source_quote":"source","predicate_logic":{"operator":"criterion","criterion_id":"p0"}}
    with pytest.raises(ValidationError,match="must not share"):
        TrialProtocolDraft.model_validate({**base,"logic_groups":[leaf,leaf]})


def test_a_single_lower_bound_cannot_discard_the_printed_upper_bound():
    for quote in ("Age 18-70 years","Age between 18 and 70 years"):
        items=drafts("inclusion",[(quote,"age","gte",18,"years")])
        prepared,_=prepare("Inclusion Criteria:\n"+quote,items)
        assert not prepared.trial.protocol_logic_supported
        assert decide(prepared,{"age":80}).confirmation_status.value=="not_confirmed"


def test_explicit_ecog_choices_survive_a_split_as_or_equalities():
    quote="ECOG performance status of 0 or 1."
    items=drafts("inclusion",[(quote,"ecog","eq",0,"score"),(quote,"ecog","eq",1,"score")])
    prepared,_=prepare("Inclusion Criteria:\n"+quote,items)
    assert prepared.trial.protocol_logic_supported,prepared.trial.protocol_logic_issues
    for value,expected in ((0,"confirmed"),(1,"confirmed"),(2,"ineligible")):
        assert decide(prepared,{"ecog":value}).confirmation_status.value==expected


def test_numeric_qualifier_cannot_be_silently_flattened_into_a_single_measurement():
    quote="eGFR >=30 mL/min/1.73m2 and no active infection."
    prepared,_=prepare("Inclusion Criteria:\n"+quote,drafts("inclusion",[(quote,"egfr","gte",30,"mL/min/1.73m2")]))
    assert not prepared.trial.protocol_logic_supported


def test_a_relation_crossing_chunks_is_not_assumed_to_be_all():
    quote="Inclusion Criteria:\nAt least one of the following:\n"+"\n".join(a[0] for a in ATOMS[:2])
    def respond(payload):
        text=payload["eligibility_text"]
        items=[r for r in drafts("inclusion") if r["source_quote"] in text]
        if not items:
            items=[{"kind":"inclusion","statement":text.strip(),"source_quote":text.strip()}]
        return {"criteria":items}
    prepared=structure_trial_protocol(TrialProtocolSource(trial_id="split-logic",title="Synthetic cross-chunk source",
        eligibility_text=quote,source_location="synthetic://cross-chunk"),
        TrialProtocolStructurerAgent(ScriptedStructuredModel({"trial_protocol_structurer":respond})),
        trace=TraceRecorder("cross-chunk"),chunk_char_limit=35)
    assert not prepared.trial.protocol_logic_supported
