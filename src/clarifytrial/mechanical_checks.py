"""Small deterministic checks for criteria that expose structured values.

This module does not read numbers, dates, concepts, or units from prose.  It
only compares fields that are already present in the public contracts.
"""

from __future__ import annotations

from datetime import date
from enum import Enum

from pydantic import Field

from .concepts import concepts_equivalent
from .contracts import (
    ClinicalStatus,
    ComparisonOperator,
    ContractModel,
    CriterionKind,
    EvidenceFact,
    EvidenceRequirement,
    EvidenceSufficiency,
    PatientState,
    TrialCriterion,
)
from .measurements import units_equivalent


class MechanicalIssueCode(str, Enum):
    """Reasons why a criterion could not be mechanically confirmed."""

    CRITERION_NOT_CONFIGURED = "criterion_not_configured"
    NO_MATCHING_EVIDENCE = "no_matching_evidence"
    UNIT_MISMATCH = "unit_mismatch"
    MISSING_EVENT_DATE = "missing_event_date"
    FUTURE_EVENT_DATE = "future_event_date"
    EVIDENCE_TOO_OLD = "evidence_too_old"
    SOURCE_NOT_ALLOWED = "source_not_allowed"
    VERIFICATION_NOT_ALLOWED = "verification_not_allowed"


class MechanicalCriterionResult(ContractModel):
    """Inspectable output of one configured numeric comparison."""

    configured: bool
    clinical_status: ClinicalStatus
    evidence_sufficiency: EvidenceSufficiency
    evidence_ids: list[str] = Field(default_factory=list)
    issue_codes: list[MechanicalIssueCode] = Field(default_factory=list)


def _compare(value: float, operator: ComparisonOperator, threshold: float) -> bool:
    comparisons = {
        ComparisonOperator.GT: value > threshold,
        ComparisonOperator.GTE: value >= threshold,
        ComparisonOperator.LT: value < threshold,
        ComparisonOperator.LTE: value <= threshold,
        ComparisonOperator.EQ: value == threshold,
    }
    return comparisons[operator]


def _clinical_status(criterion: TrialCriterion, condition_met: bool) -> ClinicalStatus:
    if criterion.kind is CriterionKind.INCLUSION:
        return ClinicalStatus.SUPPORTS if condition_met else ClinicalStatus.VIOLATES
    return ClinicalStatus.VIOLATES if condition_met else ClinicalStatus.SUPPORTS


def _requirement_issues(
    fact: EvidenceFact,
    requirement: EvidenceRequirement | None,
    as_of: date,
) -> list[MechanicalIssueCode]:
    if requirement is None:
        return []

    issues: list[MechanicalIssueCode] = []
    if requirement.max_age_days is not None:
        if fact.event_date is None:
            issues.append(MechanicalIssueCode.MISSING_EVENT_DATE)
        else:
            age_days = (as_of - fact.event_date).days
            if age_days < 0:
                issues.append(MechanicalIssueCode.FUTURE_EVENT_DATE)
            elif age_days > requirement.max_age_days:
                issues.append(MechanicalIssueCode.EVIDENCE_TOO_OLD)

    if (
        requirement.allowed_source_types is not None
        and fact.source_type not in requirement.allowed_source_types
    ):
        issues.append(MechanicalIssueCode.SOURCE_NOT_ALLOWED)

    if (
        requirement.allowed_verification_statuses is not None
        and fact.verification_status not in requirement.allowed_verification_statuses
    ):
        issues.append(MechanicalIssueCode.VERIFICATION_NOT_ALLOWED)

    return issues


def _recency_key(fact: EvidenceFact) -> tuple[date, date, str]:
    return (
        fact.event_date or date.min,
        fact.recorded_date or date.min,
        fact.evidence_id,
    )


def evaluate_criterion(
    criterion: TrialCriterion,
    patient_state: PatientState,
) -> MechanicalCriterionResult:
    """Evaluate one structured numeric criterion against visible patient facts.

    A fact is eligible only when its concept matches and its unit has the same
    meaning after notation-only normalization.  No numeric unit conversion is
    performed.  If several facts match, the newest fact satisfying the evidence
    requirement is preferred; otherwise the newest matching fact provides a
    provisional clinical direction.
    """

    constraint = criterion.numeric_constraint
    if constraint is None:
        return MechanicalCriterionResult(
            configured=False,
            clinical_status=ClinicalStatus.UNKNOWN,
            evidence_sufficiency=EvidenceSufficiency.INSUFFICIENT,
            issue_codes=[MechanicalIssueCode.CRITERION_NOT_CONFIGURED],
        )

    concept_matches = [
        fact
        for fact in patient_state.facts
        if concepts_equivalent(fact.concept, constraint.concept)
    ]
    if not concept_matches:
        return MechanicalCriterionResult(
            configured=True,
            clinical_status=ClinicalStatus.UNKNOWN,
            evidence_sufficiency=EvidenceSufficiency.INSUFFICIENT,
            issue_codes=[MechanicalIssueCode.NO_MATCHING_EVIDENCE],
        )

    unit_matches = [
        fact
        for fact in concept_matches
        if fact.unit is not None and units_equivalent(fact.unit, constraint.unit)
    ]
    if not unit_matches:
        return MechanicalCriterionResult(
            configured=True,
            clinical_status=ClinicalStatus.UNKNOWN,
            evidence_sufficiency=EvidenceSufficiency.INSUFFICIENT,
            evidence_ids=sorted(fact.evidence_id for fact in concept_matches),
            issue_codes=[MechanicalIssueCode.UNIT_MISMATCH],
        )

    facts_with_issues = [
        (
            fact,
            _requirement_issues(
                fact,
                criterion.evidence_requirement,
                patient_state.as_of.date(),
            ),
        )
        for fact in unit_matches
    ]
    sufficient_facts = [fact for fact, issues in facts_with_issues if not issues]
    if sufficient_facts:
        selected = max(sufficient_facts, key=_recency_key)
        issues: list[MechanicalIssueCode] = []
        sufficiency = EvidenceSufficiency.SUFFICIENT
    else:
        selected, issues = max(
            facts_with_issues,
            key=lambda item: _recency_key(item[0]),
        )
        sufficiency = EvidenceSufficiency.INSUFFICIENT

    assert selected.value is not None
    condition_met = _compare(
        selected.value,
        constraint.operator,
        constraint.threshold,
    )
    return MechanicalCriterionResult(
        configured=True,
        clinical_status=_clinical_status(criterion, condition_met),
        evidence_sufficiency=sufficiency,
        evidence_ids=[selected.evidence_id],
        issue_codes=issues,
    )
