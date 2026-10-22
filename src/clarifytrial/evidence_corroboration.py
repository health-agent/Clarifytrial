"""Opt-in comparison of externally identified sources for one fixed scope.

Source-family and measurement-scope declarations come from the caller, never a
model guess or document count. Agreement is not evidence that errors are truly
independent, nor a guarantee of clinical truth. The ordinary checker is unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping

from pydantic import Field

from .concepts import concepts_equivalent
from .contracts import ClinicalStatus, ContractModel, EvidenceSufficiency, PatientState, TrialCriterion
from .disclaimer import DEFAULT_MEDICAL_DISCLAIMER
from .mechanical_checks import MechanicalCriterionResult, MechanicalIssueCode, evaluate_criterion
from .measurements import converted_value


AgreementMode = Literal["exact_value", "criterion_direction"]


@dataclass(frozen=True)
class EvidenceOrigin:
    source_family: str
    comparison_scope: str

    def __post_init__(self):
        if not self.source_family.strip() or not self.comparison_scope.strip():
            raise ValueError("source family and comparison scope must be populated")
        object.__setattr__(self,"source_family",self.source_family.strip())
        object.__setattr__(self,"comparison_scope",self.comparison_scope.strip())


class CorroborationResult(ContractModel):
    assessment: MechanicalCriterionResult
    comparison_scope: str
    agreement_mode: AgreementMode = "exact_value"
    source_groups: list[str] = Field(default_factory=list)
    required_source_groups: int = Field(ge=2)
    issues: list[str] = Field(default_factory=list)
    question: str | None = None
    medical_disclaimer: str = DEFAULT_MEDICAL_DISCLAIMER


def evaluate_corroborated_criterion(
    criterion: TrialCriterion,
    patient_state: PatientState,
    *,
    origins: Mapping[str, EvidenceOrigin],
    comparison_scope: str,
    minimum_source_groups: int = 2,
    agreement_mode: AgreementMode = "exact_value",
) -> CorroborationResult:
    """Compare current sources under an explicit, criterion-specific standard.

    The caller establishes the common measurement/timepoint scope. Equal dates
    or different URLs alone do not establish that scope or source independence.
    Direction agreement establishes only a predicate, never an agreed numeric
    value. Every selected record must support the same eligibility-facing
    direction. Source multiplicity, metadata and explicit-conflict guards apply
    to both modes; different criteria may remain unresolved for the same pair.
    """
    if minimum_source_groups < 2 or not comparison_scope.strip():
        raise ValueError("corroboration needs at least two source groups and an explicit scope")
    if agreement_mode not in {"exact_value", "criterion_direction"}:
        raise ValueError("unsupported source agreement mode")
    comparison_scope=comparison_scope.strip()
    constraint = criterion.numeric_constraint
    relevant = [fact for fact in patient_state.facts if constraint is not None
                and concepts_equivalent(fact.concept, constraint.concept)]
    scoped = [fact for fact in relevant if fact.evidence_id in origins
              and origins[fact.evidence_id].comparison_scope == comparison_scope]
    scoped_state = patient_state.model_copy(update={"facts": scoped})
    base = evaluate_criterion(criterion, scoped_state)
    issues, groups, selected = [], [], []
    if any(fact.evidence_id not in origins for fact in relevant):
        issues.append("undeclared_source_identity")
    if relevant and not scoped:
        issues.append("no_evidence_in_declared_scope")
    # Evaluate each record through the ordinary date/source/verification/unit
    # guard. This keeps its policies authoritative instead of duplicating them.
    eligible, explicit_conflicts, directions_by_id = [], [], {}
    for fact in scoped:
        checked = evaluate_criterion(criterion, patient_state.model_copy(update={"facts": [fact]}))
        if checked.evidence_sufficiency is EvidenceSufficiency.SUFFICIENT and fact.event_date is not None:
            eligible.append(fact)
            directions_by_id[fact.evidence_id] = checked.clinical_status
        if (checked.evidence_sufficiency is EvidenceSufficiency.CONFLICTING and fact.event_date is not None
            and set(checked.issue_codes).issubset({MechanicalIssueCode.EVIDENCE_CONFLICT,MechanicalIssueCode.VERIFICATION_NOT_ALLOWED})):
            explicit_conflicts.append(fact)
    if eligible:
        newest = max(fact.event_date for fact in eligible)
        selected = [fact for fact in eligible if fact.event_date == newest]
        groups = sorted({origins[fact.evidence_id].source_family for fact in selected})
        values = {converted_value(fact.value, source_unit=fact.unit, target_unit=constraint.unit) for fact in selected}
        directions = {directions_by_id[fact.evidence_id] for fact in selected}
        if any(fact.event_date >= newest for fact in explicit_conflicts):
            issues.append("current_record_is_explicitly_conflicting")
            selected += [fact for fact in explicit_conflicts if fact.event_date >= newest]
            base = base.model_copy(update={"clinical_status": ClinicalStatus.UNKNOWN,
                "evidence_sufficiency": EvidenceSufficiency.CONFLICTING})
        elif len(directions) > 1 or (agreement_mode == "exact_value" and len(values) > 1):
            issues.append("source_directions_disagree" if len(directions) > 1 else "source_values_disagree")
            base = base.model_copy(update={"clinical_status": ClinicalStatus.UNKNOWN,
                "evidence_sufficiency": EvidenceSufficiency.CONFLICTING})
        elif len(groups) < minimum_source_groups:
            issues.append("additional_source_group_required")
            base = base.model_copy(update={"clinical_status": ClinicalStatus.UNKNOWN,
                "evidence_sufficiency": EvidenceSufficiency.INSUFFICIENT})
        else:
            if len(values) > 1:
                issues.append("values_differ_but_criterion_direction_agrees")
            base = base.model_copy(update={"clinical_status": next(iter(directions)),
                "evidence_sufficiency": EvidenceSufficiency.SUFFICIENT, "issue_codes": []})
    else:
        issues.append("no_current_dated_evidence_for_corroboration")
        base = base.model_copy(update={"clinical_status": ClinicalStatus.UNKNOWN,
            "evidence_sufficiency": EvidenceSufficiency.INSUFFICIENT})
    base = base.model_copy(update={"evidence_ids": sorted(f.evidence_id for f in (selected or scoped or relevant))})
    question = None
    if base.evidence_sufficiency is not EvidenceSufficiency.SUFFICIENT:
        question = (f"{criterion.numeric_constraint.concept if constraint else criterion.statement}의 같은 측정·시점 범위에 해당하는 "
                    "다른 자료원 기록을 확인해 주세요. 복사 여부·날짜·단위와 해당 조건에 대한 결론을 비교해야 합니다.")
    return CorroborationResult(assessment=base, comparison_scope=comparison_scope,
        agreement_mode=agreement_mode, source_groups=groups, required_source_groups=minimum_source_groups,
        issues=issues, question=question)
