"""Build tables, SVG figures, and Markdown directly from evaluation JSON."""

from __future__ import annotations

import csv
import json
from html import escape
from pathlib import Path
from typing import Any


def _read(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    if source.is_dir():
        source = source / "summary.json"
    value = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"report input must be a JSON object: {source}")
    return value


def _find_summary(
    rows: list[dict[str, Any]],
    *,
    policy_id: str,
    action_budget: int,
    split: str,
    input_state: str,
) -> dict[str, Any]:
    matches = [
        item
        for item in rows
        if item.get("policy_id") == policy_id
        and item.get("action_budget") == action_budget
        and item.get("split") == split
        and item.get("input_state") == input_state
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one {policy_id} summary for {split}/{input_state}/"
            f"budget={action_budget}, found {len(matches)}"
        )
    return matches[0]


def _bar_svg(title: str, subtitle: str, rows: list[tuple[str, float]]) -> str:
    width = 1100
    height = 170 + len(rows) * 92
    bar_x = 360
    bar_width = 620
    body = [
        f'<rect width="{width}" height="{height}" fill="#F5F7FB"/>',
        f'<text x="60" y="66" font-size="34" font-weight="700" fill="#172033">{escape(title)}</text>',
        f'<text x="60" y="102" font-size="18" fill="#64748B">{escape(subtitle)}</text>',
        f'<rect x="45" y="130" width="1010" height="{height - 170}" rx="18" fill="#FFFFFF"/>',
    ]
    for index, (label, value) in enumerate(rows):
        y = 170 + index * 92
        color = "#356AE6" if index == len(rows) - 1 else "#AAB5C5"
        body.extend(
            [
                f'<text x="80" y="{y + 32}" font-size="21" fill="#172033">{escape(label)}</text>',
                f'<rect x="{bar_x}" y="{y}" width="{bar_width}" height="44" rx="10" fill="#EEF2F7"/>',
                f'<rect x="{bar_x}" y="{y}" width="{round(bar_width * max(0, min(1, value)))}" height="44" rx="10" fill="{color}"/>',
                f'<text x="{bar_x + bar_width - 12}" y="{y + 30}" text-anchor="end" font-size="20" font-weight="700" fill="#172033">{value * 100:.1f}%</text>',
            ]
        )
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
            '<g font-family="Pretendard, Noto Sans KR, Malgun Gothic, sans-serif">',
            *body,
            "</g>",
            "</svg>",
            "",
        ]
    )


def build_research_report(
    *,
    destination: str | Path,
    question_policy_path: str | Path | None = None,
    burden_path: str | Path | None = None,
    workflow_path: str | Path | None = None,
    retrieval_paths: list[str | Path] | None = None,
    split: str = "heldout",
    input_state: str = "fully_missing",
    action_budget: int = 3,
) -> dict[str, Any]:
    output = Path(destination)
    output.mkdir(parents=True, exist_ok=True)
    normalized: dict[str, Any] = {
        "split": split,
        "input_state": input_state,
        "action_budget": action_budget,
    }
    metric_rows: list[dict[str, Any]] = []
    sections: list[str] = [
        "# ClarifyTrial 평가 결과",
        "",
        "이 문서는 평가 JSON에서 자동 생성됐다. 수치는 그림 코드에 따로 적지 않는다.",
        "",
    ]

    if question_policy_path is not None:
        question = _read(question_policy_path)
        fixed = _find_summary(
            question["summaries"],
            policy_id="fixed_source_order",
            action_budget=action_budget,
            split=split,
            input_state=input_state,
        )
        current = _find_summary(
            question["summaries"],
            policy_id="clarifytrial_exact_coverage_v3",
            action_budget=action_budget,
            split=split,
            input_state=input_state,
        )
        normalized["question_policy"] = {"fixed_order": fixed, "clarifytrial": current}
        for label, row in (("fixed_order", fixed), ("clarifytrial", current)):
            for name in (
                "trial_status_recovery",
                "mean_needed_fact_recall",
                "mean_unnecessary_action_count",
                "mean_action_count",
            ):
                metric_rows.append(
                    {"section": "question_policy", "arm": label, "metric": name, "value": row[name]}
                )
        (output / "question-policy.svg").write_text(
            _bar_svg(
                "세 번 확인한 뒤 최종 상태와 같아진 시험",
                f"{split} · {input_state} · 환자 {current['patient_count']}명",
                [
                    ("고정 순서", fixed["trial_status_recovery"]),
                    ("ClarifyTrial", current["trial_status_recovery"]),
                ],
            ),
            encoding="utf-8",
        )
        sections.extend(
            [
                "## 질문 순서",
                "",
                "| 방식 | 최종 상태 일치 | 필요한 정보 선택 | 결과를 더 바꾸지 않은 확인 |",
                "|---|---:|---:|---:|",
                f"| 고정 순서 | {fixed['trial_status_recovery']:.1%} | {fixed['mean_needed_fact_recall']:.1%} | 환자당 {fixed['mean_unnecessary_action_count']:.2f}회 |",
                f"| ClarifyTrial | {current['trial_status_recovery']:.1%} | {current['mean_needed_fact_recall']:.1%} | 환자당 {current['mean_unnecessary_action_count']:.2f}회 |",
                "",
                "![질문 순서 결과](question-policy.svg)",
                "",
            ]
        )

    if burden_path is not None:
        burden = _read(burden_path)
        comparison = burden["adoption_comparison"]["heldout"]
        normalized["patient_burden"] = comparison
        burden_metrics = {
            "overall_recovery_fixed": comparison["baseline_recovery"],
            "overall_recovery_adaptive": comparison["candidate_recovery"],
            "constrained_feasible_fixed": comparison["constrained_baseline_feasible_recovery"],
            "constrained_feasible_adaptive": comparison["constrained_candidate_feasible_recovery"],
            "constrained_new_test_visit_fixed": comparison["constrained_new_test_visit_baseline"],
            "constrained_new_test_visit_adaptive": comparison["constrained_new_test_visit_candidate"],
            "urgent_delay_fixed": comparison["urgent_mean_delay_baseline"],
            "urgent_delay_adaptive": comparison["urgent_mean_delay_candidate"],
        }
        for name, value in burden_metrics.items():
            metric_rows.append(
                {"section": "patient_burden", "arm": "heldout", "metric": name, "value": value}
            )
        (output / "patient-burden.svg").write_text(
            _bar_svg(
                "환자가 이용할 수 있는 방법 안에서 끝낸 시험",
                "이동·비용·새 검사 제한이 있는 합성 상황",
                [
                    ("고정 방식", comparison["constrained_baseline_feasible_recovery"]),
                    ("환자 맞춤", comparison["constrained_candidate_feasible_recovery"]),
                ],
            ),
            encoding="utf-8",
        )
        sections.extend(
            [
                "## 환자 부담을 반영한 확인 방법",
                "",
                "| 결과 | 고정 방식 | 환자 맞춤 |",
                "|---|---:|---:|",
                f"| 모든 상황의 최종 상태 일치 | {comparison['baseline_recovery']:.1%} | {comparison['candidate_recovery']:.1%} |",
                f"| 환자가 이용할 수 있는 방법 안에서 최종 상태 일치 | {comparison['constrained_baseline_feasible_recovery']:.1%} | {comparison['constrained_candidate_feasible_recovery']:.1%} |",
                f"| 새 검사·추가 방문이 어려운 상황에서 제안한 횟수 | {comparison['constrained_new_test_visit_baseline']}회 | {comparison['constrained_new_test_visit_candidate']}회 |",
                f"| 시간이 급한 상황의 평균 누적 대기 | {comparison['urgent_mean_delay_baseline']:.2f}시간 | {comparison['urgent_mean_delay_candidate']:.2f}시간 |",
                "",
                "환자 맞춤 방식은 이용하기 어려운 확인을 피하는 대신, 모든 확인 방법을 허용했을 때보다 확정되는 시험이 줄 수 있다. 두 결과를 함께 해석한다.",
                "",
                "![환자 부담 결과](patient-burden.svg)",
                "",
            ]
        )

    if workflow_path is not None:
        workflow = _read(workflow_path)
        normalized["full_workflow"] = workflow
        sections.extend(
            [
                "## 전체 에이전트 흐름",
                "",
                "| 방식 | 환자 | 최종 상태 일치 | 평균 확인 | 모델 호출 | 토큰 | 실패 |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        for row in workflow["arm_metrics"]:
            sections.append(
                f"| {row['arm']} | {row['patient_count']} | {row['trial_status_recovery']:.1%} | {row['mean_action_count']:.2f} | {row['model_call_count']} | {row['total_tokens']:,} | {row['failed_patient_count']} |"
            )
            for name in (
                "trial_status_recovery",
                "mean_action_count",
                "model_call_count",
                "total_tokens",
                "failed_patient_count",
            ):
                metric_rows.append(
                    {"section": "full_workflow", "arm": row["arm"], "metric": name, "value": row[name]}
                )
        sections.append("")

    retrieval = []
    for path in retrieval_paths or []:
        document = _read(path)
        depth_500 = next(
            item for item in document["metric_rows"] if item["depth"] == 500
        )
        row = {
            "corpus": document["config"]["corpus_name"],
            "documents": document["corpus_documents"],
            **depth_500,
        }
        retrieval.append(row)
        metric_rows.append(
            {"section": "retrieval", "arm": row["corpus"], "metric": "weighted_recall_at_500", "value": row["weighted_recall"]}
        )
    if retrieval:
        normalized["retrieval"] = retrieval
        sections.extend(
            [
                "## 관련 시험 검색",
                "",
                "| 자료 | 시험 문서 | 후보 500개 안에 남긴 비율 |",
                "|---|---:|---:|",
                *[
                    f"| {item['corpus']} | {item['documents']:,} | {item['weighted_recall']:.2%} |"
                    for item in retrieval
                ],
                "",
            ]
        )

    if len(normalized) == 3:
        raise ValueError("at least one evaluation input is required")
    (output / "summary.json").write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with (output / "metrics.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=("section", "arm", "metric", "value"))
        writer.writeheader()
        writer.writerows(metric_rows)
    (output / "report.md").write_text("\n".join(sections), encoding="utf-8")
    return {
        "output": str(output),
        "metric_count": len(metric_rows),
        "report": str(output / "report.md"),
    }


__all__ = ["build_research_report"]
