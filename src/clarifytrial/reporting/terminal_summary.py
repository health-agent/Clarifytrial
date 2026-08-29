"""Readable terminal summary built only from the saved public result."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_ROLE_LABELS = {
    "patient_record_structurer": "환자 기록 정리",
    "candidate_relevance_reviewer": "검색된 시험의 질환 확인",
    "trial_protocol_structurer": "시험 조건 정리",
    "coordinator": "진행 관리",
    "matcher_judge": "조건 판단",
    "next_evidence": "다음 확인 문장 작성",
    "selective_reviewer": "선택 검토",
}

_DECISION_LABELS = {
    ("retain", "confirmed"): "현재 자료로 조건 확인 완료",
    ("retain", "not_confirmed"): "후보 유지, 추가 확인 필요",
    ("retain", "uncertain"): "후보 유지, 판단 보류",
    ("remove", "ineligible"): "현재 조건에서 제외",
    ("uncertain", "uncertain"): "판단 보류",
}

_STOP_LABELS = {
    "all_trials_resolved": "모든 후보 시험의 현재 판단이 끝남",
    "no_pending_information": "현재 방법으로 더 확인할 정보가 없음",
    "action_limit": "정해 둔 확인 횟수를 모두 사용함",
    "awaiting_patient_choice": "환자의 선택을 기다림",
    "awaiting_clinician_authorization": "담당자의 승인을 기다림",
    "deferred": "지금 확인할 수 없어 보류함",
    "human_review": "다시 확인할 결론이 남음",
    "tool_returned_no_information": "선택한 방법으로 정보를 얻지 못함",
    "cycle_limit": "정해 둔 진행 횟수에 도달함",
}


def _decision_label(decision: Mapping[str, Any]) -> str:
    key = (
        str(decision.get("candidate_status", "")),
        str(decision.get("confirmation_status", "")),
    )
    return _DECISION_LABELS.get(key, " / ".join(key))


def _compact(items: list[str], *, limit: int = 3) -> str:
    unique = list(dict.fromkeys(item.strip() for item in items if item.strip()))
    if len(unique) <= limit:
        return "; ".join(unique)
    return "; ".join(unique[:limit]) + f"; 그 밖의 항목 {len(unique) - limit}개"


def _evidence_statements(
    decision: Mapping[str, Any],
    fact_by_id: Mapping[str, Mapping[str, Any]],
) -> list[str]:
    evidence_ids = [
        str(evidence_id)
        for assessment in decision.get("criterion_assessments", [])
        for evidence_id in assessment.get("evidence_ids", [])
    ]
    return [
        str(fact_by_id[evidence_id].get("statement", ""))
        for evidence_id in dict.fromkeys(evidence_ids)
        if evidence_id in fact_by_id
    ]


def _trial_lines(
    trial: Mapping[str, Any],
    *,
    decision_by_id: Mapping[str, Mapping[str, Any]],
    fact_by_id: Mapping[str, Mapping[str, Any]],
    titles: Mapping[str, str],
) -> list[str]:
    trial_id = str(trial["trial_id"])
    rank = trial.get("recommendation_rank")
    prefix = f"{rank}." if rank is not None else "-"
    title = titles.get(trial_id, trial_id)
    lines = [f"  {prefix} {trial_id} · {title}", f"     상태: {trial['status_label']}"]
    decision = decision_by_id.get(trial_id)
    if decision is not None:
        evidence = _compact(_evidence_statements(decision, fact_by_id))
        if evidence:
            lines.append(f"     현재 판단에 사용한 환자 정보: {evidence}")
    for item in trial.get("missing_information", []):
        methods = ", ".join(str(value) for value in item.get("confirmation_methods", []))
        suffix = f" ({methods})" if methods else ""
        lines.append(f"     아직 확인할 내용: {item['description']}{suffix}")
    return lines


def _reconsideration_lines(summary: Mapping[str, Any]) -> list[str]:
    paths = list(summary.get("change_paths", []))
    if not paths:
        return []
    lines = [f"     {summary.get('explanation', '다시 검토 조건')}"]
    status_labels = {
        "can_recheck": "나중에 다시 확인할 수 있는 경로",
        "needs_clinical_review": "기록 또는 의료진 확인이 필요한 경로",
        "no_current_path": "현재 다시 검토할 수 없는 경로",
    }
    for index, path in enumerate(paths, start=1):
        status = str(
            path.get("reconsideration_status", "needs_clinical_review")
        )
        lines.append(f"       {index}. {status_labels.get(status, '조건 경로')}")
        details = list(path.get("change_details", []))
        if details:
            for detail in details:
                lines.append(f"          - {detail.get('statement', '')}")
                lines.append(f"            {detail.get('explanation', '')}")
        else:
            changed = _compact(
                [str(item) for item in path.get("criterion_statements", [])],
                limit=4,
            )
            lines.append(f"          - {changed}")
        unconfirmed = _compact(
            [
                str(item)
                for item in path.get("still_unconfirmed_statements", [])
            ],
            limit=3,
        )
        if unconfirmed:
            lines.append(f"          이 경우 함께 확인할 내용: {unconfirmed}")
    for item in summary.get("recheck_dates", []):
        lines.append(f"     다시 확인할 날짜: {item['explanation']}")
    return lines


def build_terminal_summary_lines(
    result: Mapping[str, Any],
    *,
    titles: Mapping[str, str] | None = None,
    model_label: str | None = None,
    heading: str = "최종 결과",
) -> list[str]:
    """Return one consistent human-readable summary for every terminal entrypoint."""

    title_by_id = dict(titles or {})
    screening = result["screening"]
    decisions = list(screening["final_decisions"])
    decision_by_id = {str(item["trial_id"]): item for item in decisions}
    facts = screening.get("final_patient_state", {}).get("facts", [])
    fact_by_id = {str(item["evidence_id"]): item for item in facts}
    views = screening.get("guidance", {}).get("recommendation_views")
    if views is None:
        current = [
            {
                "trial_id": item["trial_id"],
                "status_label": _decision_label(item),
                "missing_information": [],
            }
            for item in decisions
            if item.get("candidate_status") == "retain"
            and item.get("confirmation_status") == "confirmed"
        ]
        pending_rows = [
            {
                "trial_id": item["trial_id"],
                "status_label": _decision_label(item),
                "missing_information": item.get("pending_information", []),
            }
            for item in decisions
            if item.get("candidate_status") != "remove"
            and item.get("confirmation_status") != "confirmed"
        ]
        broader = [*current, *pending_rows]
    else:
        current = list(views["current_evidence"]["trials"])
        broader = list(views["broader_review"]["trials"])
    current_ids = {str(item["trial_id"]) for item in current}
    pending = [item for item in broader if str(item["trial_id"]) not in current_ids]

    lines = [heading]
    lines.append(f"현재 자료로 조건을 확인한 시험: {len(current)}개")
    if current:
        for trial in current:
            lines.extend(
                _trial_lines(
                    trial,
                    decision_by_id=decision_by_id,
                    fact_by_id=fact_by_id,
                    titles=title_by_id,
                )
            )
    else:
        lines.append("  해당 없음")

    lines.append(f"추가 정보를 확인하면 가능성이 남는 시험: {len(pending)}개")
    if pending:
        for trial in pending:
            lines.extend(
                _trial_lines(
                    trial,
                    decision_by_id=decision_by_id,
                    fact_by_id=fact_by_id,
                    titles=title_by_id,
                )
            )
    else:
        lines.append("  해당 없음")

    removed = [item for item in decisions if item.get("candidate_status") == "remove"]
    lines.append(f"현재 조건에서 제외된 시험: {len(removed)}개")
    boundary_by_trial: dict[str, list[str]] = {}
    for item in screening.get("ineligible_boundary_differences", []):
        boundary_by_trial.setdefault(str(item["trial_id"]), []).append(
            str(item["explanation"])
        )
    reconsideration_by_trial = {
        str(item["trial_id"]): item
        for item in screening.get("trial_reconsideration_summaries", [])
    }
    for decision in removed:
        trial_id = str(decision["trial_id"])
        lines.append(f"  - {trial_id} · {title_by_id.get(trial_id, trial_id)}")
        reasons = [
            str(item.get("rationale", ""))
            for item in decision.get("criterion_assessments", [])
            if item.get("clinical_status") == "violates"
        ]
        reason = _compact(reasons, limit=2)
        if reason:
            lines.append(f"     제외 근거: {reason}")
        difference = _compact(boundary_by_trial.get(trial_id, []), limit=2)
        if difference:
            lines.append(f"     기준과의 차이: {difference}")
        reconsideration = reconsideration_by_trial.get(trial_id)
        if reconsideration is not None:
            lines.extend(_reconsideration_lines(reconsideration))

    history = list(screening.get("decision_history", []))
    lines.append("판정 변화 요약")
    changed: list[str] = []
    if history:
        initial = {
            str(item["trial_id"]): _decision_label(item)
            for item in history[0].get("decisions", [])
        }
        for decision in decisions:
            trial_id = str(decision["trial_id"])
            before = initial.get(trial_id)
            after = _decision_label(decision)
            if before is not None and before != after:
                changed.append(
                    f"{trial_id}: {before} → {after}"
                )
        if changed:
            lines.extend(f"  - {item}" for item in changed)
    if not changed:
        lines.append("  추가 정보를 반영해 바뀐 판단 없음")

    stop_reason = str(screening["stop_reason"])
    lines.append(f"종료 이유: {_STOP_LABELS.get(stop_reason, stop_reason)}")

    usage = result["usage"]
    lines.append("실행량")
    if model_label == "deterministic-workflow":
        lines.append("  코드 역할 단계")
        for role, item in usage.get("by_role", {}).items():
            lines.append(
                f"  - {_ROLE_LABELS.get(role, role)}: {item['call_count']}회 실행"
            )
        lines.append("  - 외부 모델 호출: 0회, 0토큰")
    else:
        for role, item in usage.get("by_role", {}).items():
            lines.append(
                f"  - {_ROLE_LABELS.get(role, role)}: "
                f"{item['call_count']}회, {item['total_tokens']:,}토큰"
            )
        lines.append(
            f"  - 전체 외부 모델 호출: {usage['call_count']}회, "
            f"{usage['total_tokens']:,}토큰"
        )
    return lines


__all__ = ["build_terminal_summary_lines"]
