"""Deterministic aggregation and selective-review rules.

These functions deliberately contain no confidence threshold and no model
call.  They convert criterion-level judgments into the two public trial-level
statuses using an inspectable decision table.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence

from .contracts import (
    AgentAction,
    CandidateStatus,
    ClinicalStatus,
    ConfirmationStatus,
    CriterionLogic,
    CriterionLogicEvaluation,
    CriterionLogicOperator,
    CriterionLogicStatus,
    CriterionAssessment,
    EvidenceSufficiency,
    NextAction,
    NextEvidenceRequest,
    ReviewFlag,
    ReviewReason,
    TrialCriterion,
    TrialDecision,
)


def _unique_by_id(items: Sequence[object], attribute: str, label: str) -> dict[str, object]:
    result: dict[str, object] = {}
    for item in items:
        item_id = getattr(item, attribute)
        if item_id in result:
            raise ValueError(f"duplicate {label}: {item_id}")
        result[item_id] = item
    return result


def _has_conflict(assessment: CriterionAssessment) -> bool:
    return (
        assessment.evidence_sufficiency is EvidenceSufficiency.CONFLICTING
        or ReviewFlag.EVIDENCE_CONFLICT in assessment.review_flags
    )


def _leaf_logic_status(
    assessment: CriterionAssessment | None,
) -> CriterionLogicStatus:
    if assessment is None:
        return CriterionLogicStatus.UNRESOLVED
    if _has_conflict(assessment):
        return CriterionLogicStatus.CONFLICTING
    if assessment.evidence_sufficiency is not EvidenceSufficiency.SUFFICIENT:
        return CriterionLogicStatus.UNRESOLVED
    if assessment.clinical_status in {
        ClinicalStatus.SUPPORTS,
        ClinicalStatus.NOT_APPLICABLE,
    }:
        return CriterionLogicStatus.SATISFIED
    if assessment.clinical_status is ClinicalStatus.VIOLATES:
        return CriterionLogicStatus.VIOLATED
    return CriterionLogicStatus.UNRESOLVED


def _group_logic_status(
    operator: CriterionLogicOperator,
    child_statuses: Sequence[CriterionLogicStatus],
    minimum_required: int | None,
) -> CriterionLogicStatus:
    satisfied = child_statuses.count(CriterionLogicStatus.SATISFIED)
    violated = child_statuses.count(CriterionLogicStatus.VIOLATED)
    unresolved = child_statuses.count(CriterionLogicStatus.UNRESOLVED)
    conflicting = child_statuses.count(CriterionLogicStatus.CONFLICTING)

    if operator is CriterionLogicOperator.ALL:
        if violated:
            return CriterionLogicStatus.VIOLATED
        if satisfied == len(child_statuses):
            return CriterionLogicStatus.SATISFIED
        if conflicting and unresolved == 0:
            return CriterionLogicStatus.CONFLICTING
        return CriterionLogicStatus.UNRESOLVED

    if operator is CriterionLogicOperator.ANY:
        if satisfied:
            return CriterionLogicStatus.SATISFIED
        if violated == len(child_statuses):
            return CriterionLogicStatus.VIOLATED
        if conflicting and unresolved == 0:
            return CriterionLogicStatus.CONFLICTING
        return CriterionLogicStatus.UNRESOLVED

    if operator is not CriterionLogicOperator.AT_LEAST:
        raise ValueError(f"unsupported criterion logic operator: {operator.value}")
    assert minimum_required is not None
    if satisfied >= minimum_required:
        return CriterionLogicStatus.SATISFIED
    if satisfied + unresolved + conflicting < minimum_required:
        return CriterionLogicStatus.VIOLATED
    if satisfied + unresolved < minimum_required and conflicting:
        return CriterionLogicStatus.CONFLICTING
    return CriterionLogicStatus.UNRESOLVED


def evaluate_criterion_logic(
    logic: CriterionLogic,
    assessments: Sequence[CriterionAssessment],
) -> CriterionLogicEvaluation:
    """Evaluate a nested eligibility expression without flattening its branches."""

    assessment_by_id = _unique_by_id(
        assessments, "criterion_id", "assessment criterion_id"
    )

    def evaluate(node: CriterionLogic) -> CriterionLogicEvaluation:
        if node.operator is CriterionLogicOperator.CRITERION:
            assert node.criterion_id is not None
            return CriterionLogicEvaluation(
                operator=node.operator,
                status=_leaf_logic_status(assessment_by_id.get(node.criterion_id)),
                criterion_id=node.criterion_id,
                label=node.label,
            )
        children = [evaluate(child) for child in node.children]
        return CriterionLogicEvaluation(
            operator=node.operator,
            status=_group_logic_status(
                node.operator,
                [child.status for child in children],
                node.minimum_required,
            ),
            label=node.label,
            minimum_required=node.minimum_required,
            children=children,
        )

    return evaluate(logic)


def _validate_logic_references(
    criteria: Sequence[TrialCriterion],
    logic: CriterionLogic,
) -> None:
    known = {criterion.criterion_id for criterion in criteria}
    referenced = logic.referenced_criterion_ids()
    unknown = referenced - known
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ValueError(f"criterion logic refers to unknown criteria: {names}")
    missing_required = {
        criterion.criterion_id
        for criterion in criteria
        if criterion.required and criterion.criterion_id not in referenced
    }
    if missing_required:
        names = ", ".join(sorted(missing_required))
        raise ValueError(f"criterion logic omits required criteria: {names}")


def aggregate_statuses(
    criteria: Sequence[TrialCriterion],
    assessments: Sequence[CriterionAssessment],
    eligibility_logic: CriterionLogic | None = None,
) -> tuple[CandidateStatus, ConfirmationStatus]:
    """Apply the published decision table to required criteria.

    Precedence is conflict, sufficient violation, complete support, then
    incomplete evidence.  Optional criteria do not change the trial-level
    status.  A required criterion without an assessment is incomplete rather
    than negative.
    """

    criterion_by_id = _unique_by_id(criteria, "criterion_id", "criterion_id")
    assessment_by_id = _unique_by_id(
        assessments, "criterion_id", "assessment criterion_id"
    )

    unknown_criteria = set(assessment_by_id) - set(criterion_by_id)
    if unknown_criteria:
        names = ", ".join(sorted(unknown_criteria))
        raise ValueError(f"assessments refer to unknown criteria: {names}")

    if eligibility_logic is not None:
        _validate_logic_references(criteria, eligibility_logic)
        result = evaluate_criterion_logic(eligibility_logic, assessments)
        status_map = {
            CriterionLogicStatus.SATISFIED: (
                CandidateStatus.RETAIN,
                ConfirmationStatus.CONFIRMED,
            ),
            CriterionLogicStatus.VIOLATED: (
                CandidateStatus.REMOVE,
                ConfirmationStatus.INELIGIBLE,
            ),
            CriterionLogicStatus.UNRESOLVED: (
                CandidateStatus.RETAIN,
                ConfirmationStatus.NOT_CONFIRMED,
            ),
            CriterionLogicStatus.CONFLICTING: (
                CandidateStatus.UNCERTAIN,
                ConfirmationStatus.UNCERTAIN,
            ),
        }
        return status_map[result.status]

    required = [criterion for criterion in criteria if criterion.required]
    if not required:
        raise ValueError("at least one required criterion is needed")

    required_assessments = [
        assessment_by_id[criterion.criterion_id]
        for criterion in required
        if criterion.criterion_id in assessment_by_id
    ]

    if any(_has_conflict(assessment) for assessment in required_assessments):
        return CandidateStatus.UNCERTAIN, ConfirmationStatus.UNCERTAIN

    has_sufficient_violation = any(
        assessment.clinical_status is ClinicalStatus.VIOLATES
        and assessment.evidence_sufficiency is EvidenceSufficiency.SUFFICIENT
        for assessment in required_assessments
    )
    if has_sufficient_violation:
        return CandidateStatus.REMOVE, ConfirmationStatus.INELIGIBLE

    all_required_supported = len(required_assessments) == len(required) and all(
        assessment.clinical_status is ClinicalStatus.SUPPORTS
        and assessment.evidence_sufficiency is EvidenceSufficiency.SUFFICIENT
        for assessment in required_assessments
    )
    if all_required_supported:
        return CandidateStatus.RETAIN, ConfirmationStatus.CONFIRMED

    return CandidateStatus.RETAIN, ConfirmationStatus.NOT_CONFIRMED


def select_review_reasons(
    *,
    criteria: Sequence[TrialCriterion],
    assessments: Sequence[CriterionAssessment],
    candidate_status: CandidateStatus,
    confirmation_status: ConfirmationStatus,
    available_evidence_ids: Collection[str] | None = None,
) -> list[ReviewReason]:
    """Return structural reasons for calling the optional review agent.

    Ordinary unknown or insufficient information does not call the reviewer.
    Review is limited to explicit defect flags, conflicts, source mismatches,
    missing citations, code/model mismatches, and evidence defects supporting a
    decisive removal or confirmation.
    """

    criterion_by_id = _unique_by_id(criteria, "criterion_id", "criterion_id")
    _unique_by_id(assessments, "criterion_id", "assessment criterion_id")
    known_evidence = (
        None if available_evidence_ids is None else set(available_evidence_ids)
    )
    reasons: list[ReviewReason] = []

    def add(reason: ReviewReason) -> None:
        if reason not in reasons:
            reasons.append(reason)

    defects_by_criterion: dict[str, bool] = {}

    for assessment in assessments:
        criterion = criterion_by_id.get(assessment.criterion_id)
        if criterion is None:
            raise ValueError(
                f"assessment refers to unknown criterion: {assessment.criterion_id}"
            )

        has_defect = False
        if assessment.review_flags:
            add(ReviewReason.EXPLICIT_FLAG)
            has_defect = True

        if (
            assessment.evidence_sufficiency is EvidenceSufficiency.CONFLICTING
            or ReviewFlag.EVIDENCE_CONFLICT in assessment.review_flags
        ):
            add(ReviewReason.EVIDENCE_CONFLICT)
            has_defect = True

        if ReviewFlag.CODE_MODEL_MISMATCH in assessment.review_flags:
            add(ReviewReason.CODE_MODEL_MISMATCH)
            has_defect = True

        source_mismatch = (
            assessment.criterion_source_location != criterion.source_location
            or ReviewFlag.CRITERION_SOURCE_MISMATCH in assessment.review_flags
        )
        if source_mismatch:
            add(ReviewReason.CRITERION_SOURCE_MISMATCH)
            has_defect = True

        makes_evidence_claim = (
            assessment.clinical_status
            in {ClinicalStatus.SUPPORTS, ClinicalStatus.VIOLATES}
            and assessment.evidence_sufficiency is EvidenceSufficiency.SUFFICIENT
        )
        references_missing = known_evidence is not None and any(
            evidence_id not in known_evidence
            for evidence_id in assessment.evidence_ids
        )
        evidence_missing = (
            ReviewFlag.MISSING_EVIDENCE in assessment.review_flags
            or (makes_evidence_claim and not assessment.evidence_ids)
            or references_missing
        )
        if evidence_missing:
            add(ReviewReason.MISSING_EVIDENCE)
            has_defect = True

        defects_by_criterion[assessment.criterion_id] = has_defect

    decisive_ids: set[str] = set()
    if candidate_status is CandidateStatus.REMOVE:
        decisive_ids = {
            assessment.criterion_id
            for assessment in assessments
            if assessment.clinical_status is ClinicalStatus.VIOLATES
            and assessment.evidence_sufficiency is EvidenceSufficiency.SUFFICIENT
        }
    elif confirmation_status is ConfirmationStatus.CONFIRMED:
        decisive_ids = {
            criterion.criterion_id for criterion in criteria if criterion.required
        }

    if any(defects_by_criterion.get(criterion_id, False) for criterion_id in decisive_ids):
        add(ReviewReason.DECISIVE_RESULT_EVIDENCE_DEFECT)

    return reasons


def requires_selective_review(
    *,
    criteria: Sequence[TrialCriterion],
    assessments: Sequence[CriterionAssessment],
    candidate_status: CandidateStatus,
    confirmation_status: ConfirmationStatus,
    available_evidence_ids: Collection[str] | None = None,
) -> bool:
    """Return whether the structural review rule selects this decision."""

    return bool(
        select_review_reasons(
            criteria=criteria,
            assessments=assessments,
            candidate_status=candidate_status,
            confirmation_status=confirmation_status,
            available_evidence_ids=available_evidence_ids,
        )
    )


def aggregate_trial_decision(
    *,
    trial_id: str,
    criteria: Sequence[TrialCriterion],
    assessments: Sequence[CriterionAssessment],
    pending_information: Sequence[NextEvidenceRequest] = (),
    next_action: AgentAction | None = None,
    available_evidence_ids: Collection[str] | None = None,
    eligibility_logic: CriterionLogic | None = None,
) -> TrialDecision:
    """Build a complete, traceable trial decision without a model call."""

    if any(criterion.trial_id != trial_id for criterion in criteria):
        raise ValueError("every criterion must belong to the requested trial")

    candidate_status, confirmation_status = aggregate_statuses(
        criteria,
        assessments,
        eligibility_logic,
    )
    logic_evaluation = (
        None
        if eligibility_logic is None
        else evaluate_criterion_logic(eligibility_logic, assessments)
    )
    review_reasons = select_review_reasons(
        criteria=criteria,
        assessments=assessments,
        candidate_status=candidate_status,
        confirmation_status=confirmation_status,
        available_evidence_ids=available_evidence_ids,
    )

    resolved = confirmation_status in {
        ConfirmationStatus.CONFIRMED,
        ConfirmationStatus.INELIGIBLE,
    }
    effective_pending = [] if resolved else list(pending_information)
    if resolved:
        no_action_reason = "현재 결과에는 추가 확인 행동이 필요하지 않다."
    elif effective_pending:
        no_action_reason = "다음 확인 행동을 아직 선택하지 않았다."
    else:
        no_action_reason = "현재 결과를 바꿀 추가 확인 항목이 없다."
    resolved_action = next_action or AgentAction(
        action=NextAction.NONE,
        reason=no_action_reason,
    )
    return TrialDecision(
        trial_id=trial_id,
        candidate_status=candidate_status,
        confirmation_status=confirmation_status,
        criterion_assessments=list(assessments),
        pending_information=effective_pending,
        next_action=resolved_action,
        review_required=bool(review_reasons),
        review_reasons=review_reasons,
        logic_evaluation=logic_evaluation,
    )
