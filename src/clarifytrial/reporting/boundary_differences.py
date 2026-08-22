"""Calculate plain arithmetic gaps for decisive structured criteria."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ..contracts import (
    BoundaryPosition,
    ClinicalStatus,
    ComparisonOperator,
    ConfirmationStatus,
    CriterionBoundaryDifference,
    EvidenceFact,
    EvidenceSufficiency,
    PatientState,
    TrialCriterion,
    TrialDecision,
)
from ..mechanical_checks import evaluate_criterion


_OPERATOR_LABEL = {
    ComparisonOperator.GT: "초과",
    ComparisonOperator.GTE: "이상",
    ComparisonOperator.LT: "미만",
    ComparisonOperator.LTE: "이하",
    ComparisonOperator.EQ: "같음",
}


def _number(value: float) -> str:
    return f"{value:g}"


def _position(value: float, threshold: float) -> BoundaryPosition:
    if value < threshold:
        return BoundaryPosition.BELOW
    if value > threshold:
        return BoundaryPosition.ABOVE
    return BoundaryPosition.EQUAL


def _explanation(
    *,
    criterion: TrialCriterion,
    value: float,
    threshold: float,
    unit: str,
) -> str:
    kind = "선정 조건" if criterion.kind.value == "inclusion" else "제외 조건"
    operator = _OPERATOR_LABEL[criterion.numeric_constraint.operator]
    difference = abs(value - threshold)
    if value < threshold:
        relation = f"기준보다 {_number(difference)} {unit} 낮습니다."
    elif value > threshold:
        relation = f"기준보다 {_number(difference)} {unit} 높습니다."
    else:
        relation = "현재 값은 기준값과 같습니다."
    return (
        f"{kind}은 {_number(threshold)} {unit} {operator}입니다. "
        f"현재 값은 {_number(value)} {unit}이며, {relation}"
    )


def build_ineligible_boundary_differences(
    *,
    patient_state: PatientState,
    decisions: Sequence[TrialDecision],
    criteria_by_id: Mapping[str, TrialCriterion],
) -> list[CriterionBoundaryDifference]:
    """Describe cutoff differences only for decisive, sufficient violations."""

    evidence_by_id: dict[str, EvidenceFact] = {
        item.evidence_id: item for item in patient_state.facts
    }
    result: list[CriterionBoundaryDifference] = []
    for decision in sorted(decisions, key=lambda item: item.trial_id):
        if decision.confirmation_status is not ConfirmationStatus.INELIGIBLE:
            continue
        for assessment in decision.criterion_assessments:
            criterion = criteria_by_id.get(assessment.criterion_id)
            if criterion is None or criterion.numeric_constraint is None:
                continue
            if criterion.numeric_constraint.unit.strip().lower() in {"bool", "boolean"}:
                continue
            if (
                assessment.clinical_status is not ClinicalStatus.VIOLATES
                or assessment.evidence_sufficiency
                is not EvidenceSufficiency.SUFFICIENT
            ):
                continue
            checked = evaluate_criterion(criterion, patient_state)
            if (
                checked.clinical_status is not ClinicalStatus.VIOLATES
                or checked.evidence_sufficiency
                is not EvidenceSufficiency.SUFFICIENT
                or not set(checked.evidence_ids).issubset(assessment.evidence_ids)
            ):
                continue
            evidence = evidence_by_id.get(checked.evidence_ids[0])
            if evidence is None or evidence.value is None or evidence.unit is None:
                continue
            threshold = criterion.numeric_constraint.threshold
            difference = evidence.value - threshold
            result.append(
                CriterionBoundaryDifference(
                    trial_id=decision.trial_id,
                    criterion_id=criterion.criterion_id,
                    criterion_kind=criterion.kind,
                    criterion_statement=criterion.statement,
                    criterion_source_location=criterion.source_location,
                    evidence_id=evidence.evidence_id,
                    current_value=evidence.value,
                    threshold=threshold,
                    unit=criterion.numeric_constraint.unit,
                    operator=criterion.numeric_constraint.operator,
                    position=_position(evidence.value, threshold),
                    difference_from_threshold=difference,
                    absolute_difference=abs(difference),
                    explanation=_explanation(
                        criterion=criterion,
                        value=evidence.value,
                        threshold=threshold,
                        unit=criterion.numeric_constraint.unit,
                    ),
                )
            )
    return result


__all__ = ["build_ineligible_boundary_differences"]
