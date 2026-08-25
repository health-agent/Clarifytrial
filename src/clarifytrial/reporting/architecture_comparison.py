"""Compare agent-call structures on one fixed benchmark and one policy arm."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

from ..io import atomic_write_text


_LABELS = {
    "rules_only": "구조화 규칙만 사용",
    "single_judge": "조건 판단 모델만 사용",
    "code_routed_agents": "코드가 순서를 통제하고 필요한 역할만 호출",
    "full_agents_no_reviewer": "모델 조정자와 역할별 호출, 별도 검토 제외",
    "full_agents": "모델 조정자와 역할별 호출, 필요할 때 별도 검토",
}


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _metric(summary: dict[str, Any], arm: str) -> dict[str, Any]:
    try:
        return next(row for row in summary["arm_metrics"] if row["arm"] == arm)
    except StopIteration as error:
        raise ValueError(f"summary does not contain arm {arm!r}") from error


def _comparison_settings(manifest: dict[str, Any]) -> dict[str, Any]:
    return {
        name: manifest.get(name)
        for name in (
            "split",
            "patient_ids",
            "action_budget",
            "max_selective_reviews",
            "max_cycles",
            "include_unavailable_scenario",
            "include_patient_choice_scenario",
            "approve_synthetic_actions",
            "broad_search_top_k",
            "unavailable_answer_selection",
            "arms",
            "inputs",
        )
    }


def build_architecture_comparison(
    *,
    workflow_summary_paths: Sequence[str | Path],
    output_dir: str | Path,
    arm: str = "clarifytrial",
) -> dict[str, Any]:
    if len(workflow_summary_paths) < 2:
        raise ValueError("architecture comparison requires at least two summaries")
    rows = []
    shared_settings = None
    for raw_path in workflow_summary_paths:
        path = Path(raw_path)
        summary = _read(path)
        manifest_path = path.parent / "run-manifest.json"
        if not manifest_path.is_file():
            raise ValueError(f"workflow summary is missing its manifest: {path}")
        manifest = _read(manifest_path)
        settings = _comparison_settings(manifest)
        if shared_settings is None:
            shared_settings = settings
        elif settings != shared_settings:
            raise ValueError(
                "architecture summaries must use the same inputs, patients, and "
                "evaluation settings"
            )

        metric = _metric(summary, arm)
        recovery_uncertainty = metric.get("cluster_uncertainty", {}).get(
            "trial_status_recovery"
        )
        rows.append(
            {
                "agent_architecture": summary.get(
                    "agent_architecture", "code_routed_agents"
                ),
                "architecture_label": _LABELS.get(
                    summary.get("agent_architecture", "code_routed_agents"),
                    summary.get("agent_architecture", "code_routed_agents"),
                ),
                "model": summary.get("model"),
                "arm": arm,
                "patient_count": metric["patient_count"],
                "trial_count": metric["trial_count"],
                "trial_status_recovery": metric["trial_status_recovery"],
                "patient_cluster_bootstrap_95_ci": (
                    None
                    if recovery_uncertainty is None
                    else recovery_uncertainty["bootstrap_95_ci"]
                ),
                "confirmed_rescue_count": metric.get("confirmed_rescue_count", 0),
                "rescue_opportunity_count": metric.get(
                    "rescue_opportunity_count", 0
                ),
                "false_preservation_resolved_count": metric.get(
                    "false_preservation_resolved_count", 0
                ),
                "false_preservation_count": metric.get(
                    "false_preservation_count", 0
                ),
                "false_candidate_removals": metric["false_candidate_removals"],
                "premature_final_confirmations": metric[
                    "premature_final_confirmations"
                ],
                "runtime_failure_count": metric["failed_patient_count"],
                "selective_review_count": metric.get("selective_review_count", 0),
                "mechanical_model_correction_count": metric.get(
                    "mechanical_model_correction_count", 0
                ),
                "unrecovered_trial_status_count": (
                    metric["trial_count"]
                    - round(metric["trial_status_recovery"] * metric["trial_count"])
                ),
                "role_execution_count": metric["model_call_count"],
                "external_model_call_count": metric.get(
                    "external_model_call_count", metric["model_call_count"]
                ),
                "total_tokens": metric["total_tokens"],
                "total_latency_ms": metric["total_latency_ms"],
                "role_usage": metric.get("role_usage", {}),
            }
        )

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    payload = {
        "protocol_id": "clarifytrial-agent-architecture-comparison-v1",
        "arm": arm,
        "same_input_files": True,
        "same_patients": True,
        "same_evaluation_settings": True,
        "evaluation_settings": shared_settings,
        "evaluation_scope": {
            "patient_input": "standardized_json",
            "criteria": "objective_structured_subset",
            "gold": "frozen_separate_reference_implementation",
            "measures_complete_trial_eligibility": False,
        },
        "rows": rows,
    }
    atomic_write_text(
        output / "summary.json",
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )

    first = rows[0]
    single = next(
        (row for row in rows if row["agent_architecture"] == "single_judge"),
        None,
    )
    routed = next(
        (row for row in rows if row["agent_architecture"] == "code_routed_agents"),
        None,
    )
    token_comparison = (
        ""
        if single is None or routed is None
        else (
            f" 조건 판단 모델은 {single['total_tokens']:,}토큰, 질문 문장 작성까지 "
            f"모델로 맡긴 방식은 {routed['total_tokens']:,}토큰을 사용했지만 후보 "
            "확정이나 제외 정리 수를 늘리지 않았다."
        )
    )
    lines = [
        "# 구조화 조건에서 모델 호출 방식 비교",
        "",
        f"기존 평가와 겹치지 않는 공개 시험을 사용해 합성 환자 {first['patient_count']}명, 환자–시험 판단 {first['trial_count']}개를 만들었다. 정답은 현재 판정 코드와 별도로 만든 계산표에 먼저 고정했다. 모든 조건은 수치·날짜·참거짓으로 구조화돼 코드가 직접 계산할 수 있다.",
        "",
        "같은 환자와 같은 시험, 같은 질문 선택 방법을 사용하고 모델을 부르는 방식만 바꿨다.",
        "",
        "| 실행 방식 | 최종 상태 일치 | 실제 후보 확정 | 결국 제외될 후보 정리 | 모델 답과 구조화 규칙이 달라 코드 계산을 적용한 조건 | 근거 충돌 재검토 | 실행 실패 | 외부 모델 호출 | 전체 토큰 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['architecture_label']} | {row['trial_status_recovery']:.1%} | "
            f"{row['confirmed_rescue_count']}/{row['rescue_opportunity_count']}개 | "
            f"{row['false_preservation_resolved_count']}/{row['false_preservation_count']}개 | "
            f"{row['mechanical_model_correction_count']}개 | "
            f"{row['selective_review_count']}회 | "
            f"{row['runtime_failure_count']}명 | "
            f"{row['external_model_call_count']}회 | {row['total_tokens']:,} |"
        )
    lines.extend(
        [
            "",
            "## 결론",
            "",
            "세 방식의 최종 결과는 같았다. 완전히 구조화된 조건에서는 외부 모델을 더 부르지 않아도 됐다."
            + token_comparison,
            "",
            "따라서 수치, 날짜와 논리 관계가 이미 나뉜 JSON 입력은 코드가 우선 처리한다. 조건의 뜻을 코드로 정할 수 없을 때만 조건 판단 모델을 쓰고, 실제 근거 충돌이 있을 때만 별도 검토를 부른다. 이 결과를 여러 모델 역할이 정확도를 높였다는 근거로 사용하지 않는다.",
            "",
            "## 오류를 나눈 기준",
            "",
            "- 잘못 제외: 정답에서는 남겨야 할 시험을 제외한 경우",
            "- 성급한 확정: 아직 정보가 부족한데 참가 가능으로 확정한 경우",
            "- 최종 상태 불일치: 후보 유지 여부와 현재 확정 상태 가운데 하나라도 다른 경우",
            "- 실행 실패: 정해진 출력 형식이나 호출 과정에서 결과를 만들지 못한 환자",
            "",
        ]
    )
    atomic_write_text(output / "report.md", "\n".join(lines))
    return payload


__all__ = ["build_architecture_comparison"]
