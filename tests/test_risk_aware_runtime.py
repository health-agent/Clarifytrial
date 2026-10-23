from datetime import datetime, timezone

from clarifytrial.contracts import EvidenceRequirement, NumericConstraint, PatientState, TrialCriterion, VerificationStatus
from clarifytrial.evidence_corroboration import EvidenceOrigin
from clarifytrial.interactive.contracts import InteractivePolicyView, InteractiveTrial
from clarifytrial.interactive.paper_benchmark import evidence
from clarifytrial.interactive.risk_aware_runtime import propose_risk_aware_action, RiskAwareRuntimeResult
from clarifytrial.disclaimer import DEFAULT_MEDICAL_DISCLAIMER


def run(facts, *, confirmed=True, origins=None, families=("record","verification"), remaining=3):
    criterion=TrialCriterion(criterion_id="c",trial_id="t",kind="inclusion",statement="Synthetic marker >= 1 score",
        source_location="synthetic-protocol#c",numeric_constraint=NumericConstraint(concept="marker",operator="gte",threshold=1,unit="score"),
        evidence_requirement=EvidenceRequirement(max_age_days=14,allowed_source_types=["medical_record"],allowed_verification_statuses=["verified"]))
    view=InteractivePolicyView(case_id="synthetic",disease_group="synthetic",action_budget=3,
        trials=[InteractiveTrial(trial_id="t",criteria=[criterion])])
    patient=PatientState(patient_id="SYNTHETIC",as_of=datetime(2026,10,22,12,tzinfo=timezone.utc),facts=facts)
    result=propose_risk_aware_action(view,patient,domains={"marker":(0.,1.,2.)},
        probabilities={"marker":(.2,.3,.5)},origins=origins or {f.evidence_id:EvidenceOrigin("record","fixed") for f in facts},
        scope_by_concept={"marker":"fixed"},route_families=families,source_meaning_confirmed={"t":confirmed},
        risk_cutoff=.10,remaining_actions=remaining)
    return result


def test_valid_record_roundtrip_preserves_evidence_source_and_disclaimer_without_clinical_confirmation():
    fact=evidence("marker",2.,"score")
    result=run([fact]);proposal,=result.proposals
    assert proposal.research_status=="subset_supported"
    assert proposal.evidence_ids==[fact.evidence_id]
    assert proposal.criterion_source_locations==["synthetic-protocol#c"]
    assert result.full_protocol_confirmation_allowed is False
    assert result.score_is_calibrated_clinical_risk is False
    assert result.medical_disclaimer==DEFAULT_MEDICAL_DISCLAIMER
    assert RiskAwareRuntimeResult.model_validate_json(result.model_dump_json())==result


def test_stale_or_wrong_scope_cannot_become_a_current_source_report():
    old=evidence("marker",2.,"score",outcome="stale")
    result=run([old],remaining=0)
    assert result.proposals[0].research_status=="abstain"
    assert result.proposals[0].pending_information
    assert result.proposals[0].evidence_ids==[]
    fresh=evidence("marker",2.,"score")
    result=run([fresh],origins={fresh.evidence_id:EvidenceOrigin("record","another-timepoint")},remaining=0)
    assert result.proposals[0].research_status=="abstain"


def test_unrepresented_source_meaning_leaves_a_protocol_question_and_no_patient_action():
    result=run([evidence("marker",2.,"score")],confirmed=False)
    assert result.proposals[0].pending_information[0].fact_id=="protocol:t:source-meaning"
    assert result.next_action.action.value=="NONE"


def test_explicit_current_conflict_is_not_dropped_in_favor_of_another_report():
    good=evidence("marker",2.,"score",sequence=1)
    bad=evidence("marker",0.,"score",sequence=2).model_copy(update={"verification_status":VerificationStatus.CONFLICTING})
    result=run([good,bad],remaining=0)
    assert result.proposals[0].research_status=="abstain"
    assert any("current_explicit_conflict" in issue for issue in result.issues)


def test_continuous_out_of_domain_value_is_not_rounded_to_a_favorable_grid_cell():
    result=run([evidence("marker",1.2,"score")],remaining=0)
    assert result.proposals[0].research_status=="abstain"
    assert any("outside_declared_finite_domain" in issue for issue in result.issues)
