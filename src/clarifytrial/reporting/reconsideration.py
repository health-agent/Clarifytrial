"""Explain what would have to change before an excluded trial is revisited."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import timedelta
from itertools import product
from typing import TYPE_CHECKING

from ..contracts import (
    ClinicalStatus,
    ComparisonOperator,
    ConfirmationStatus,
    CriterionAssessment,
    CriterionChangeDetail,
    CriterionChangeKind,
    CriterionChangePath,
    CriterionKind,
    CriterionLogic,
    CriterionLogicEvaluation,
    CriterionLogicOperator,
    CriterionLogicStatus,
    CriterionRecheckDate,
    EvidenceFact,
    EvidenceSufficiency,
    PatientState,
    ReconsiderationPathStatus,
    TrialCriterion,
    TrialDecision,
    TrialReconsiderationSummary,
    VerificationStatus,
)
from ..decision_rules import evaluate_criterion_logic
from ..mechanical_checks import evaluate_criterion

if TYPE_CHECKING:
    from ..workflow.patient_screening_contracts import ScreeningTrial


_MAX_CHANGE_PATHS = 256
_ELAPSED_CONCEPT = re.compile(
    r"(?i)(?:days?_since|weeks?_since|time_since|elapsed|washout|duration|"
    r"stable_.*(?:days?|weeks?)|maintained_.*(?:days?|weeks?))"
)
_ELAPSED_STATEMENT = re.compile(
    r"(?i)(?:\bsince\b|\bafter\b|\belapsed\b|\bwashout\b|\bduration\b|"
    r"경과|이후|지난 기간|유지 기간|중단 후)"
)
_FIXED_HISTORY = re.compile(
    r"(?i)(?:\bhistory\s+of\b|\bprior\b|\bprevious(?:ly)?\b|\bever\b|"
    r"\bcongenital\b|\bgenetic\b|\bage\s+at\b|\bbirth\b|과거|병력|선천|유전)"
)
_CLINICAL_STATE_OR_PROCEDURE = re.compile(
    r"(?i)(?:\bcurrent\b|\bactive\b|\bwilling\b|\bconsent\b|\bplanned\b|"
    r"\bscheduled\b|\bprocedure\b|\bsurgery\b|현재|활동성|동의|예정|시술|수술)"
)


@dataclass(frozen=True, slots=True)
class _RouteRequirement:
    changes: frozenset[str]
    confirmations: frozenset[str]


def _route_key(item: _RouteRequirement) -> tuple[object, ...]:
    return (
        len(item.changes) + len(item.confirmations),
        len(item.changes),
        tuple(sorted(item.changes)),
        tuple(sorted(item.confirmations)),
    )


def _prune_routes(
    routes: Sequence[_RouteRequirement],
) -> tuple[list[_RouteRequirement], bool]:
    """Remove duplicates and routes that require everything another route does."""

    kept: list[_RouteRequirement] = []
    for route in sorted(set(routes), key=_route_key):
        if any(
            prior.changes.issubset(route.changes)
            and prior.confirmations.issubset(route.confirmations)
            for prior in kept
        ):
            continue
        kept.append(route)
    truncated = len(kept) > _MAX_CHANGE_PATHS
    return kept[:_MAX_CHANGE_PATHS], truncated


def _combine_all(
    groups: Sequence[Sequence[_RouteRequirement]],
) -> tuple[list[_RouteRequirement], bool]:
    routes = [_RouteRequirement(frozenset(), frozenset())]
    truncated = False
    for group in groups:
        routes, cut = _prune_routes(
            [
                _RouteRequirement(
                    left.changes | right.changes,
                    left.confirmations | right.confirmations,
                )
                for left, right in product(routes, group)
            ]
        )
        truncated = truncated or cut
    return routes, truncated


def _requirements_for_logic(
    logic: CriterionLogic,
    evaluation: CriterionLogicEvaluation,
) -> tuple[list[_RouteRequirement], bool]:
    if logic.operator is CriterionLogicOperator.CRITERION:
        assert logic.criterion_id is not None
        if evaluation.status is CriterionLogicStatus.VIOLATED:
            return [
                _RouteRequirement(frozenset({logic.criterion_id}), frozenset())
            ], False
        if evaluation.status in {
            CriterionLogicStatus.UNRESOLVED,
            CriterionLogicStatus.CONFLICTING,
        }:
            return [
                _RouteRequirement(frozenset(), frozenset({logic.criterion_id}))
            ], False
        return [_RouteRequirement(frozenset(), frozenset())], False

    child_results = [
        _requirements_for_logic(child_logic, child_evaluation)
        for child_logic, child_evaluation in zip(
            logic.children,
            evaluation.children,
            strict=True,
        )
    ]
    child_routes = [item[0] for item in child_results]
    truncated = any(item[1] for item in child_results)
    if logic.operator is CriterionLogicOperator.ALL:
        routes, cut = _combine_all(child_routes)
        return routes, truncated or cut
    if logic.operator is CriterionLogicOperator.ANY:
        routes, cut = _prune_routes(
            [route for group in child_routes for route in group]
        )
        return routes, truncated or cut

    assert logic.operator is CriterionLogicOperator.AT_LEAST
    assert logic.minimum_required is not None
    states: dict[int, list[_RouteRequirement]] = {
        0: [_RouteRequirement(frozenset(), frozenset())]
    }
    for group in child_routes:
        updated = {count: list(routes) for count, routes in states.items()}
        for count, routes in states.items():
            combined, cut = _combine_all((routes, group))
            updated.setdefault(count + 1, []).extend(combined)
            updated[count + 1], cut_again = _prune_routes(updated[count + 1])
            truncated = truncated or cut or cut_again
        states = updated
    return states[logic.minimum_required], truncated


def _default_logic(criteria: Sequence[TrialCriterion]) -> CriterionLogic:
    return CriterionLogic(
        operator=CriterionLogicOperator.ALL,
        children=[
            CriterionLogic(
                operator=CriterionLogicOperator.CRITERION,
                criterion_id=item.criterion_id,
            )
            for item in criteria
            if item.required
        ],
    )


def _path_explanation(
    changed: Sequence[TrialCriterion],
    unconfirmed: Sequence[TrialCriterion],
    status: ReconsiderationPathStatus,
) -> str:
    changed_text = "; ".join(item.statement for item in changed)
    if status is ReconsiderationPathStatus.NO_CURRENT_PATH:
        result = (
            "이 경로에는 되돌리기 어려운 조건이 있어 현재 다시 검토할 수 없습니다: "
            + changed_text
        )
    elif status is ReconsiderationPathStatus.NEEDS_CLINICAL_REVIEW:
        result = (
            "이 경로가 나중에 달라질 수 있는지는 기록 또는 의료진 확인이 필요합니다: "
            + changed_text
        )
    else:
        result = f"나중에 다시 확인할 수 있는 조건은 다음과 같습니다: {changed_text}"
    if unconfirmed:
        result += (
            ". 다음 조건은 아직 자료가 없어 함께 확인해야 합니다: "
            + "; ".join(item.statement for item in unconfirmed)
        )
    return result + "."


def _age_change_kind(criterion: TrialCriterion) -> CriterionChangeKind:
    constraint = criterion.numeric_constraint
    assert constraint is not None
    if criterion.kind is CriterionKind.INCLUSION:
        if constraint.operator in {ComparisonOperator.GTE, ComparisonOperator.GT}:
            return CriterionChangeKind.ELAPSED_TIME
        return CriterionChangeKind.FIXED_OR_HISTORICAL
    if constraint.operator in {ComparisonOperator.LT, ComparisonOperator.LTE}:
        return CriterionChangeKind.ELAPSED_TIME
    return CriterionChangeKind.FIXED_OR_HISTORICAL


def _change_detail(criterion: TrialCriterion) -> CriterionChangeDetail:
    constraint = criterion.numeric_constraint
    concept = "" if constraint is None else constraint.concept.casefold()
    statement = criterion.statement
    if constraint is not None and "age" in concept:
        kind = _age_change_kind(criterion)
    elif constraint is not None and (
        _ELAPSED_CONCEPT.search(constraint.concept)
        or _ELAPSED_STATEMENT.search(statement)
    ):
        kind = CriterionChangeKind.ELAPSED_TIME
    elif _FIXED_HISTORY.search(" ".join((concept, statement))):
        kind = CriterionChangeKind.FIXED_OR_HISTORICAL
    elif constraint is not None:
        kind = CriterionChangeKind.RECHECKABLE_MEASUREMENT
    elif _CLINICAL_STATE_OR_PROCEDURE.search(statement):
        kind = CriterionChangeKind.CLINICAL_STATE_OR_PROCEDURE
    else:
        kind = CriterionChangeKind.UNCLEAR

    explanations = {
        CriterionChangeKind.RECHECKABLE_MEASUREMENT: (
            "검사나 측정값을 나중에 다시 확인할 수 있지만 값이 좋아진다고 예측하지는 않습니다."
        ),
        CriterionChangeKind.ELAPSED_TIME: (
            "시간이 지나면 조건이 달라질 수 있습니다. 정확한 날짜는 근거가 충분할 때만 계산합니다."
        ),
        CriterionChangeKind.FIXED_OR_HISTORICAL: (
            "나이 또는 이미 일어난 이력처럼 되돌릴 수 없어 현재 다시 검토할 경로로 보지 않습니다."
        ),
        CriterionChangeKind.CLINICAL_STATE_OR_PROCEDURE: (
            "현재 상태나 절차가 달라질 수 있는지는 기록 또는 의료진 확인이 필요합니다."
        ),
        CriterionChangeKind.UNCLEAR: (
            "현재 구조만으로는 달라질 수 있는 조건인지 계산할 수 없습니다."
        ),
    }
    return CriterionChangeDetail(
        criterion_id=criterion.criterion_id,
        statement=statement,
        kind=kind,
        explanation=explanations[kind],
    )


def _path_status(
    details: Sequence[CriterionChangeDetail],
) -> ReconsiderationPathStatus:
    kinds = {item.kind for item in details}
    if CriterionChangeKind.FIXED_OR_HISTORICAL in kinds:
        return ReconsiderationPathStatus.NO_CURRENT_PATH
    if kinds.issubset(
        {
            CriterionChangeKind.RECHECKABLE_MEASUREMENT,
            CriterionChangeKind.ELAPSED_TIME,
        }
    ):
        return ReconsiderationPathStatus.CAN_RECHECK
    return ReconsiderationPathStatus.NEEDS_CLINICAL_REVIEW


def _days_per_unit(unit: str) -> int | None:
    normalized = unit.strip().casefold().rstrip(".")
    if normalized in {"d", "day", "days", "일"}:
        return 1
    if normalized in {"wk", "wks", "week", "weeks", "주"}:
        return 7
    return None


def _waiting_target(criterion: TrialCriterion) -> float | None:
    constraint = criterion.numeric_constraint
    assert constraint is not None
    if criterion.kind is CriterionKind.INCLUSION:
        if constraint.operator is ComparisonOperator.GTE:
            return constraint.threshold
        if constraint.operator is ComparisonOperator.GT:
            return math.floor(constraint.threshold) + 1
        return None
    if constraint.operator is ComparisonOperator.LT:
        return constraint.threshold
    if constraint.operator is ComparisonOperator.LTE:
        return math.floor(constraint.threshold) + 1
    return None


def _build_recheck_date(
    *,
    trial_id: str,
    criterion: TrialCriterion,
    assessment: CriterionAssessment,
    patient_state: PatientState,
    evidence_by_id: Mapping[str, EvidenceFact],
) -> CriterionRecheckDate | None:
    constraint = criterion.numeric_constraint
    if constraint is None or assessment.clinical_status is not ClinicalStatus.VIOLATES:
        return None
    if assessment.evidence_sufficiency is not EvidenceSufficiency.SUFFICIENT:
        return None
    days_per_unit = _days_per_unit(constraint.unit)
    if days_per_unit is None:
        return None
    if not (
        _ELAPSED_CONCEPT.search(constraint.concept)
        or _ELAPSED_STATEMENT.search(criterion.statement)
    ):
        return None
    checked = evaluate_criterion(criterion, patient_state)
    if (
        checked.clinical_status is not ClinicalStatus.VIOLATES
        or checked.evidence_sufficiency is not EvidenceSufficiency.SUFFICIENT
        or not checked.evidence_ids
        or not set(checked.evidence_ids).issubset(assessment.evidence_ids)
    ):
        return None
    evidence = evidence_by_id.get(checked.evidence_ids[0])
    if (
        evidence is None
        or evidence.value is None
        or evidence.verification_status is not VerificationStatus.VERIFIED
    ):
        return None
    target = _waiting_target(criterion)
    if target is None or evidence.value >= target:
        return None
    days_remaining = math.ceil((target - evidence.value) * days_per_unit)
    if days_remaining < 1:
        return None
    recheck_date = patient_state.as_of.date() + timedelta(days=days_remaining)
    assumption = (
        "그 사이 같은 유형의 새 사건이 생기지 않고 현재 경과기간이 계속 늘어난다는 조건"
    )
    unit_label = "일" if days_per_unit == 1 else "주"
    return CriterionRecheckDate(
        trial_id=trial_id,
        criterion_id=criterion.criterion_id,
        evidence_id=evidence.evidence_id,
        current_elapsed=evidence.value,
        required_elapsed=target,
        unit=constraint.unit,
        days_remaining=days_remaining,
        recheck_date=recheck_date,
        assumption=assumption,
        explanation=(
            f"현재 경과기간은 {evidence.value:g}{unit_label}이고 다시 확인할 기준은 "
            f"{target:g}{unit_label}입니다. {assumption}이라면 "
            f"{recheck_date.isoformat()}부터 다시 확인할 수 있습니다."
        ),
    )


def build_trial_reconsideration_summaries(
    *,
    patient_state: PatientState,
    decisions: Sequence[TrialDecision],
    trials: Sequence["ScreeningTrial"],
) -> list[TrialReconsiderationSummary]:
    """Build transparent change paths and dates for currently excluded trials."""

    trial_by_id = {item.trial_id: item for item in trials}
    evidence_by_id = {item.evidence_id: item for item in patient_state.facts}
    summaries: list[TrialReconsiderationSummary] = []
    for decision in sorted(decisions, key=lambda item: item.trial_id):
        if decision.confirmation_status is not ConfirmationStatus.INELIGIBLE:
            continue
        trial = trial_by_id.get(decision.trial_id)
        if trial is None or not trial.protocol_logic_supported:
            continue
        criteria_by_id = {item.criterion_id: item for item in trial.criteria}
        logic = trial.eligibility_logic or _default_logic(trial.criteria)
        evaluation = evaluate_criterion_logic(logic, decision.criterion_assessments)
        routes, truncated = _requirements_for_logic(logic, evaluation)
        routes = [item for item in routes if item.changes]
        if not routes:
            continue
        paths = []
        for route in routes:
            changed = [criteria_by_id[item] for item in sorted(route.changes)]
            unconfirmed = [
                criteria_by_id[item] for item in sorted(route.confirmations)
            ]
            details = [_change_detail(item) for item in changed]
            path_status = _path_status(details)
            paths.append(
                CriterionChangePath(
                    criterion_ids=[item.criterion_id for item in changed],
                    criterion_statements=[item.statement for item in changed],
                    reconsideration_status=path_status,
                    change_details=details,
                    still_unconfirmed_criterion_ids=[
                        item.criterion_id for item in unconfirmed
                    ],
                    still_unconfirmed_statements=[
                        item.statement for item in unconfirmed
                    ],
                    explanation=_path_explanation(
                        changed,
                        unconfirmed,
                        path_status,
                    ),
                )
            )
        assessment_by_id = {
            item.criterion_id: item for item in decision.criterion_assessments
        }
        changed_ids = {
            criterion_id for route in routes for criterion_id in route.changes
        }
        recheck_dates = []
        for criterion_id in sorted(changed_ids):
            assessment = assessment_by_id.get(criterion_id)
            if assessment is None:
                continue
            recheck = _build_recheck_date(
                trial_id=decision.trial_id,
                criterion=criteria_by_id[criterion_id],
                assessment=assessment,
                patient_state=patient_state,
                evidence_by_id=evidence_by_id,
            )
            if recheck is not None:
                recheck_dates.append(recheck)
        minimum = min(len(item.criterion_ids) for item in paths)
        usable_paths = [
            item
            for item in paths
            if item.reconsideration_status
            is not ReconsiderationPathStatus.NO_CURRENT_PATH
        ]
        summaries.append(
            TrialReconsiderationSummary(
                trial_id=decision.trial_id,
                minimum_change_count=minimum,
                change_paths=paths,
                paths_truncated=truncated,
                recheck_dates=recheck_dates,
                explanation=(
                    (
                        f"현재 확인된 조건 가운데 적어도 {minimum}개가 달라져야 "
                        "이 시험을 다시 검토할 수 있습니다."
                    )
                    if usable_paths
                    else (
                        f"조건 조합상 적어도 {minimum}개가 달라져야 하지만, "
                        "현재 다시 검토할 수 있는 경로는 확인되지 않았습니다."
                    )
                ),
            )
        )
    return summaries


__all__ = ["build_trial_reconsideration_summaries"]
