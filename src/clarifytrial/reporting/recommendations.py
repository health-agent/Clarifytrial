"""Build two explicit recommendation views from the same trial decisions.

The current-evidence view contains only trials whose supplied criteria are
confirmed.  The broader-review view also contains trials that remain plausible
but need more information.  No language model call is used here, so the lists
cannot drift away from the validated trial statuses.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..contracts import (
    CandidateStatus,
    ConfirmationStatus,
    MissingInformationSummary,
    RecommendationList,
    RecommendationViews,
    TrialDecision,
    TrialRecommendationSummary,
)


_ACTION_LABELS = {
    "LOOKUP_RECORD": "기존 기록 확인",
    "ASK_PATIENT": "환자에게 직접 확인",
    "REQUEST_VERIFICATION": "공식 검사 결과 또는 의료진 확인",
    "DEFER": "현재 단계에서 보류",
}


def _missing_information(decision: TrialDecision) -> list[MissingInformationSummary]:
    return [
        MissingInformationSummary(
            fact_id=item.fact_id,
            description=item.description,
            confirmation_methods=[
                _ACTION_LABELS[action.value] for action in item.acceptable_actions
            ],
        )
        for item in decision.pending_information
    ]


def _confirmed_summary(decision: TrialDecision) -> TrialRecommendationSummary:
    return TrialRecommendationSummary(
        trial_id=decision.trial_id,
        status_label="현재 자료로 조건 확인 완료",
        explanation=(
            "현재 제공된 자료에서는 이 사전 검토 범위의 필수 조건이 확인되었습니다. "
            "실제 등록 가능 여부는 시험 연구진이 전체 자료로 다시 확인해야 합니다."
        ),
        missing_information=[],
    )


def _pending_summary(decision: TrialDecision) -> TrialRecommendationSummary:
    missing = _missing_information(decision)
    if missing:
        explanation = f"후보로 남아 있으며 확인할 정보가 {len(missing)}개 남아 있습니다."
    else:
        explanation = (
            "후보로 남아 있지만 자료 충돌 또는 사람의 검토가 남아 있어 현재 확정할 "
            "수 없습니다."
        )
    return TrialRecommendationSummary(
        trial_id=decision.trial_id,
        status_label="추가 확인 필요",
        explanation=explanation,
        missing_information=missing,
    )


def build_recommendation_views(
    decisions: Iterable[TrialDecision],
) -> RecommendationViews:
    """Return narrow and broad lists with an inspectable inclusion rule."""

    confirmed: list[TrialRecommendationSummary] = []
    pending: list[TrialRecommendationSummary] = []
    for decision in sorted(decisions, key=lambda item: item.trial_id):
        removed = (
            decision.candidate_status is CandidateStatus.REMOVE
            or decision.confirmation_status is ConfirmationStatus.INELIGIBLE
        )
        if removed:
            continue
        if (
            decision.candidate_status is CandidateStatus.RETAIN
            and decision.confirmation_status is ConfirmationStatus.CONFIRMED
        ):
            confirmed.append(_confirmed_summary(decision))
        else:
            pending.append(_pending_summary(decision))

    return RecommendationViews(
        current_evidence=RecommendationList(
            title="현재 자료만 기준으로 확인된 임상시험",
            explanation=(
                "현재 제공된 자료에서 이 사전 검토 범위의 필수 조건이 확인된 시험만 "
                "표시합니다."
            ),
            trials=confirmed,
        ),
        broader_review=RecommendationList(
            title="가능성을 넓게 보아 계속 검토할 임상시험",
            explanation=(
                "현재 확인된 시험과, 가능성은 있지만 추가 정보가 필요한 시험을 함께 "
                "표시합니다. 추가 확인 후보를 참가 가능으로 확정한 것은 아닙니다."
            ),
            trials=[*confirmed, *pending],
        ),
    )
