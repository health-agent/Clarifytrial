"""Opt-in research path connecting source comparison to ordinary decisions."""
from __future__ import annotations

from typing import Mapping

from pydantic import Field

from ..contracts import AgentAction, ContractModel, CriterionAssessment, NextAction, NextEvidenceRequest, PatientState, TrialDecision
from ..decision_rules import aggregate_trial_decision
from ..disclaimer import DEFAULT_MEDICAL_DISCLAIMER
from ..evidence_corroboration import CorroborationResult, EvidenceOrigin, evaluate_corroborated_criterion
from .contracts import InteractivePolicyView


class CorroboratedViewResult(ContractModel):
    decisions: list[TrialDecision]
    criteria: dict[str, CorroborationResult]
    pending_evidence_requests: list[NextEvidenceRequest] = Field(default_factory=list)
    assessment_scope: str = "selected_structured_conditions_only"
    full_protocol_confirmation_allowed: bool = False
    medical_disclaimer: str = DEFAULT_MEDICAL_DISCLAIMER


def evaluate_corroborated_view(view: InteractivePolicyView, state: PatientState, *,
        origins: Mapping[str, EvidenceOrigin], scope_by_concept: Mapping[str, str]) -> CorroboratedViewResult:
    results, decisions, criterion_by_id = {}, [], {}
    for trial in view.trials:
        assessments = []
        for criterion in trial.criteria:
            criterion_by_id[criterion.criterion_id]=criterion
            if criterion.numeric_constraint is None:
                raise ValueError("research comparison requires a structured numeric criterion")
            concept = criterion.numeric_constraint.concept
            result = evaluate_corroborated_criterion(criterion, state, origins=origins,
                comparison_scope=scope_by_concept[concept])
            results[criterion.criterion_id] = result
            checked = result.assessment
            assessments.append(CriterionAssessment(criterion_id=criterion.criterion_id,
                criterion_source_location=criterion.source_location, clinical_status=checked.clinical_status,
                evidence_sufficiency=checked.evidence_sufficiency, evidence_ids=checked.evidence_ids,
                rationale="Declared-source comparison for the fixed synthetic measurement scope; agreement does not prove truth."))
        decisions.append(aggregate_trial_decision(trial_id=trial.trial_id, criteria=trial.criteria,
            assessments=assessments, eligibility_logic=trial.eligibility_logic,
            next_action=AgentAction(action=NextAction.NONE, reason="Research acquisition is selected separately."),
            available_evidence_ids={fact.evidence_id for fact in state.facts}))
    needed={}
    def unresolved(node):
        if node.status.value not in {"unresolved","conflicting"}:return set()
        if node.criterion_id is not None:return {node.criterion_id}
        return set().union(*(unresolved(child) for child in node.children))
    for decision in decisions:
        if decision.confirmation_status.value in {"confirmed","ineligible"}:continue
        ids=(unresolved(decision.logic_evaluation) if decision.logic_evaluation is not None else
             {a.criterion_id for a in decision.criterion_assessments if a.evidence_sufficiency.value!="sufficient"})
        for cid in sorted(ids):
            concept=criterion_by_id[cid].numeric_constraint.concept
            key=(concept,results[cid].comparison_scope)
            needed.setdefault(key,[]).append(cid)
    requests=[]
    for (concept,scope),ids in sorted(needed.items()):
        requests.append(NextEvidenceRequest(fact_id=f"corroborate:{scope}:{concept}",
            description=results[ids[0]].question or "해당 측정 범위의 다른 자료원을 확인해 주세요.",
            related_criterion_ids=ids,acceptable_actions=[NextAction.LOOKUP_RECORD,NextAction.REQUEST_VERIFICATION],
            reason="동일 범위의 자료원 부족·불일치가 남아 있다. 복사본은 새 자료원으로 세지 않는다."))
    decisions=[decision.model_copy(update={"pending_information":[request for request in requests
        if any(criterion_by_id[cid].trial_id==decision.trial_id for cid in request.related_criterion_ids)]})
        if decision.confirmation_status.value not in {"confirmed","ineligible"} else decision for decision in decisions]
    return CorroboratedViewResult(decisions=decisions, criteria=results,pending_evidence_requests=requests)
