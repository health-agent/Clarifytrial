"""Opt-in typed bridge from validated records to research acquisition proposals.

Does not set clinical confirmed/ineligible statuses. Protocol meaning, finite
domains, source families and common measurement scope must be caller declared.
The ordinary metadata/date/unit checker is authoritative for each record.
"""
from __future__ import annotations

from typing import Literal, Mapping

from pydantic import Field

from ..contracts import (AgentAction, ContractModel, EvidenceSufficiency, NextAction,
    NextEvidenceRequest, PatientState)
from ..disclaimer import DEFAULT_MEDICAL_DISCLAIMER
from ..evidence_corroboration import EvidenceOrigin
from ..mechanical_checks import evaluate_criterion
from ..measurements import converted_value
from .contracts import InteractivePolicyView
from .paper_benchmark import LogicalRule, PublicAcquisitionModel, ScalarRule
from .risk_aware_acquisition import RiskAwareAcquisition


class RiskAwareTrialProposal(ContractModel):
    trial_id: str
    research_status: Literal["subset_supported", "subset_violated", "abstain"]
    modeled_error_upper: float | None = None
    evidence_ids: list[str] = Field(default_factory=list)
    criterion_source_locations: list[str]
    pending_information: list[NextEvidenceRequest] = Field(default_factory=list)


class RiskAwareRuntimeResult(ContractModel):
    proposals: list[RiskAwareTrialProposal]
    next_action: AgentAction
    issues: list[str]
    assessment_scope: str = "research_selected_numeric_subsets"
    full_protocol_confirmation_allowed: bool = False
    score_is_calibrated_clinical_risk: bool = False
    medical_disclaimer: str = DEFAULT_MEDICAL_DISCLAIMER


def propose_risk_aware_action(view: InteractivePolicyView, patient: PatientState, *,
        domains: Mapping[str, tuple[float, ...]], probabilities: Mapping[str, tuple[float, ...]],
        origins: Mapping[str, EvidenceOrigin], scope_by_concept: Mapping[str, str],
        route_families: tuple[str, str], source_meaning_confirmed: Mapping[str, bool],
        attempted_routes: frozenset[tuple[str, int]] = frozenset(), remaining_actions=3,
        remaining_visits=1, risk_cutoff=.03) -> RiskAwareRuntimeResult:
    """Build inspectable proposals; no tool execution or prior-only confirmation."""
    supported=[t for t in view.trials if source_meaning_confirmed.get(t.trial_id,False)]
    proposals=[];issues=[]
    for trial in view.trials:
        if trial in supported:continue
        question=NextEvidenceRequest(fact_id=f"protocol:{trial.trial_id}:source-meaning",
            description="시험 원문의 연결·범위·시점을 원문과 시험 담당자에게 확인해 주세요.",
            related_criterion_ids=[c.criterion_id for c in trial.criteria],
            acceptable_actions=[NextAction.REQUEST_VERIFICATION],reason="환자 자료를 더 모아도 원문 의미의 부족은 해결되지 않습니다.")
        proposals.append(RiskAwareTrialProposal(trial_id=trial.trial_id,research_status="abstain",
            criterion_source_locations=[c.source_location for c in trial.criteria],pending_information=[question]))
    action=AgentAction(action=NextAction.NONE,reason="지원하는 조건의 새로운 확인 행동이 없습니다.")
    if not supported:return RiskAwareRuntimeResult(proposals=proposals,next_action=action,issues=issues)
    if any(c.numeric_constraint is None for t in supported for c in t.criteria):
        raise ValueError("research bridge requires source-checked numeric criteria")
    codes=tuple(sorted({c.numeric_constraint.concept for t in supported for c in t.criteria}))
    rules=[];logic=[];criteria_by_concept={code:[] for code in codes}
    for trial in supported:
        indices={c.criterion_id:i for i,c in enumerate(trial.criteria)}
        rules.append(tuple(ScalarRule(codes.index(c.numeric_constraint.concept),c.numeric_constraint.operator.value,
            c.numeric_constraint.threshold,c.kind.value=="exclusion") for c in trial.criteria))
        def tree(node):
            return LogicalRule(node.operator.value,indices.get(node.criterion_id),tuple(tree(c) for c in node.children))
        logic.append(tree(trial.eligibility_logic) if trial.eligibility_logic else LogicalRule("all",
            children=tuple(LogicalRule("criterion",i) for i in range(len(trial.criteria)))))
        for c in trial.criteria:criteria_by_concept[c.numeric_constraint.concept].append(c)
    model=PublicAcquisitionModel(codes,tuple(domains[c] for c in codes),tuple(probabilities[c] for c in codes),tuple(rules),tuple(logic))
    state=[-2]*(2*len(codes));ids_by_fact={i:[] for i in range(len(codes))}
    for i,code in enumerate(codes):
        cs=criteria_by_concept[code]
        units={c.numeric_constraint.unit for c in cs}
        if len(units)!=1:raise ValueError("normalize criteria to one declared unit per concept first")
        unit=next(iter(units));valid=[];conflicts=[]
        for fact in patient.facts:
            if fact.concept!=code:continue
            origin=origins.get(fact.evidence_id)
            if origin is None or origin.comparison_scope!=scope_by_concept[code] or origin.source_family not in route_families:
                issues.append(f"{fact.evidence_id}:undeclared_or_wrong_source_scope");continue
            checks=[evaluate_criterion(c,patient.model_copy(update={"facts":[fact]})) for c in cs]
            if fact.verification_status.value=="conflicting" and fact.event_date is not None:
                conflicts.append(fact)
            if not all(c.evidence_sufficiency is EvidenceSufficiency.SUFFICIENT for c in checks):
                issues.append(f"{fact.evidence_id}:metadata_date_unit_or_conflict_guard");continue
            value=converted_value(fact.value,source_unit=fact.unit,target_unit=unit)
            if value not in domains[code]:
                issues.append(f"{fact.evidence_id}:outside_declared_finite_domain");continue
            valid.append((fact,origin,domains[code].index(value)))
        newest=max((f.event_date for f,_,_ in valid),default=None)
        if any(newest is None or fact.event_date>=newest for fact in conflicts):
            state[2*i]=state[2*i+1]=-1
            ids_by_fact[i].extend(f.evidence_id for f in conflicts)
            issues.append(f"{code}:current_explicit_conflict_requires_review")
            continue
        valid=[entry for entry in valid if entry[0].event_date==newest]
        for route,family in enumerate(route_families):
            records=[entry for entry in valid if entry[1].source_family==family]
            values={value for _,_,value in records}
            if len(values)>1:
                issues.append(f"{code}:conflicting_current_values_within_source_family")
                # A conflict within one family is not two independent reports.
                state[2*i]=state[2*i+1]=-1;break
            if records:
                target=route_families.index(family)
                state[2*i+target]=next(iter(values))
                ids_by_fact[i].extend(f.evidence_id for f,_,_ in records)
            elif (code,route) in attempted_routes:state[2*i+route]=-1
    planner=RiskAwareAcquisition(model,risk_cutoff=risk_cutoff,same_source_family=route_families[0]==route_families[1])
    fixed=(-1,)*len(codes);state=tuple(state)
    for trial,decision in zip(supported,planner.decisions(fixed,state)):
        questions=[]
        if decision.label<0:
            questions.append(NextEvidenceRequest(fact_id=f"risk-review:{trial.trial_id}",
                description="현재 조건의 결론을 바꿀 수 있는 자료를 추가 확인하거나 다른 자료원으로 대조해 주세요.",
                related_criterion_ids=[c.criterion_id for c in trial.criteria],
                acceptable_actions=[NextAction.LOOKUP_RECORD,NextAction.REQUEST_VERIFICATION],
                reason=decision.reason))
        proposals.append(RiskAwareTrialProposal(trial_id=trial.trial_id,
            research_status={-1:"abstain",0:"subset_violated",1:"subset_supported"}[decision.label],
            modeled_error_upper=decision.model_error_upper,
            evidence_ids=sorted({eid for f in decision.evidence_facts for eid in ids_by_fact[f]}),
            criterion_source_locations=[c.source_location for c in trial.criteria],pending_information=questions))
    selected=planner.choose(fixed,state,remaining_visits,remaining_actions)
    if selected is not None:
        fact,route=selected;code=codes[fact]
        action=AgentAction(action=NextAction.LOOKUP_RECORD if route==0 else NextAction.REQUEST_VERIFICATION,
            target_fact_id=f"risk-source:{route_families[route]}:{scope_by_concept[code]}:{code}",
            related_criterion_ids=[c.criterion_id for c in criteria_by_concept[code]],
            reason="예산·방문 한도 안에서 관측된 부분조건의 보류를 줄이기 위한 연구용 확인 제안입니다.")
    planner.clear_caches()
    return RiskAwareRuntimeResult(proposals=sorted(proposals,key=lambda p:p.trial_id),next_action=action,issues=sorted(set(issues)))
