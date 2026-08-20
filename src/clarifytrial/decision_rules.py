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


def aggregate_statuses(
    criteria: Sequence[TrialCriterion],
    assessments: Sequence[CriterionAssessment],
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
) -> TrialDecision:
    """Build a complete, traceable trial decision without a model call."""

    if any(criterion.trial_id != trial_id for criterion in criteria):
        raise ValueError("every criterion must belong to the requested trial")

    candidate_status, confirmation_status = aggregate_statuses(criteria, assessments)
    review_reasons = select_review_reasons(
        criteria=criteria,
        assessments=assessments,
        candidate_status=candidate_status,
        confirmation_status=confirmation_status,
        available_evidence_ids=available_evidence_ids,
    )

    if confirmation_status in {
        ConfirmationStatus.CONFIRMED,
        ConfirmationStatus.INELIGIBLE,
    }:
        no_action_reason = "현재 결과에는 추가 확인 행동이 필요하지 않다."
    elif pending_information:
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
        pending_information=list(pending_information),
        next_action=resolved_action,
        review_required=bool(review_reasons),
        review_reasons=review_reasons,
    )
