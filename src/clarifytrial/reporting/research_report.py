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
    height = 170 + len(rows) * 108
    bar_x = 640
    bar_width = 340
    body = [
        f'<rect width="{width}" height="{height}" fill="#F5F7FB"/>',
        f'<text x="60" y="66" font-size="29" font-weight="700" fill="#172033">{escape(title)}</text>',
        f'<text x="60" y="102" font-size="18" fill="#64748B">{escape(subtitle)}</text>',
        f'<rect x="45" y="130" width="1010" height="{height - 170}" rx="18" fill="#FFFFFF"/>',
    ]
    for index, (label, value) in enumerate(rows):
        y = 170 + index * 108
        color = "#356AE6" if index == len(rows) - 1 else "#AAB5C5"
        label_lines = label.split("\n")
        label_spans = "".join(
            f'<tspan x="80" dy="{0 if line_index == 0 else 26}">{escape(line)}</tspan>'
            for line_index, line in enumerate(label_lines)
        )
        body.extend(
            [
                f'<text x="80" y="{y + 18}" font-size="19" fill="#172033">{label_spans}</text>',
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
        "# ClarifyTrial 실험 결과: 부족한 환자 정보를 어떤 순서와 방법으로 확인했는가",
        "",
        "이 보고서는 불완전한 환자 자료에서 어떤 정보를 먼저 확인할지, 환자의 이동·비용·시간 제한을 반영하면 확인 방법이 어떻게 달라지는지, 그 결과 임상시험 판단이 얼마나 더 끝나는지를 보여 준다.",
        "수치는 저장된 평가 JSON에서 읽으며 그림이나 표에 따로 복사해 넣지 않는다.",
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
                f"추가 정보를 {action_budget}번 확인한 뒤 판단을 끝낸 시험",
                f"처음 자료가 불완전한 합성 환자 {current['patient_count']}명 · 추가 확인 최대 {action_budget}번",
                [
                    (
                        f"처음 빠진 정보 목록의 앞 {action_budget}개를\n적힌 순서대로 확인",
                        fixed["trial_status_recovery"],
                    ),
                    (
                        f"남은 {action_budget}번 안에 가장 많은 시험 판단을 끝낼\n정보를 매번 다시 계산",
                        current["trial_status_recovery"],
                    ),
                ],
            ),
            encoding="utf-8",
        )
        sections.extend(
            [
                "## 부족한 환자 정보 가운데 무엇을 먼저 확인할지 비교",
                "",
                f"처음 자료가 불완전한 합성 환자 {current['patient_count']}명에게 추가 확인을 최대 {action_budget}번 허용했다. 합성자료에는 가려 둔 답을 반영했을 때 각 시험이 놓일 상태가 미리 정해져 있으며, 질문 뒤 같은 상태에 도달하면 판단을 끝낸 것으로 셌다.",
                "",
                "| 부족한 정보를 고른 방법 | 확인 횟수 안에 판단을 끝낸 시험 비율 | 최종 판단에 실제로 필요했던 정보 중 확인한 비율 | 확인했지만 어떤 시험의 판단도 더 바꾸지 못한 정보 수 |",
                "|---|---:|---:|---:|",
                f"| 처음 빠진 정보 목록의 앞 {action_budget}개를 적힌 순서대로 확인 | {fixed['trial_status_recovery']:.1%} | {fixed['mean_needed_fact_recall']:.1%} | 환자당 {fixed['mean_unnecessary_action_count']:.2f}개 |",
                f"| 남은 {action_budget}번 안에 가장 많은 시험 판단을 끝낼 정보 조합을 매번 다시 계산 | {current['trial_status_recovery']:.1%} | {current['mean_needed_fact_recall']:.1%} | 환자당 {current['mean_unnecessary_action_count']:.2f}개 |",
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
                "환자가 이용할 수 있는 방법만 사용해 판단을 끝낸 시험",
                "이동·비용 부담 때문에 새 검사나 추가 방문을 피해야 하는 합성 상황",
                [
                    (
                        "모든 환자에게 같은 비용표를 적용해\n확인 방법을 선택",
                        comparison["constrained_baseline_feasible_recovery"],
                    ),
                    (
                        "환자의 이동·비용·시간 제한을 반영해\n확인 방법을 선택",
                        comparison["constrained_candidate_feasible_recovery"],
                    ),
                ],
            ),
            encoding="utf-8",
        )
        sections.extend(
            [
                "## 같은 정보를 어떤 방법으로 확인할지 환자 상황에 맞춰 선택",
                "",
                "같은 부족 정보라도 기존 병원 기록 조회, 환자 답변, 새 검사처럼 여러 확인 방법이 있을 수 있다. 모든 환자에게 같은 비용표를 적용하는 방법과, 환자가 입력한 이동·비용·시간 제한을 먼저 반영하는 방법을 비교했다.",
                "",
                "| 무엇을 측정했는가 | 모든 환자에게 같은 비용표를 적용해 확인 방법을 선택 | 환자의 이동·비용·시간 제한을 반영해 확인 방법을 선택 |",
                "|---|---:|---:|",
                f"| 이동·비용 제한이 없는 상황까지 모두 합쳤을 때 판단을 끝낸 시험 비율 | {comparison['baseline_recovery']:.1%} | {comparison['candidate_recovery']:.1%} |",
                f"| 환자가 실제로 이용할 수 있는 방법만 사용해 판단을 끝낸 시험 비율 | {comparison['constrained_baseline_feasible_recovery']:.1%} | {comparison['constrained_candidate_feasible_recovery']:.1%} |",
                f"| 환자가 새 검사나 추가 방문을 피해야 한다고 입력했는데도 그런 방법을 선택한 횟수 | {comparison['constrained_new_test_visit_baseline']}회 | {comparison['constrained_new_test_visit_candidate']}회 |",
                f"| 시간이 급하다고 입력한 환자에게 선택한 확인 방법의 예상 대기시간 합계 | {comparison['urgent_mean_delay_baseline']:.2f}시간 | {comparison['urgent_mean_delay_candidate']:.2f}시간 |",
                "",
                "환자의 제한을 반영하면 실행하기 어려운 검사나 방문은 피할 수 있지만, 확인하지 못한 정보가 남아 시험 판단을 끝내지 못할 수도 있다. 따라서 전체 판단 완료 비율과 환자 제한을 지킨 상태의 판단 완료 비율을 함께 본다.",
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
                "## 관련 시험 검색부터 질문 뒤 재판정까지 전체 프로그램을 실행한 결과",
                "",
                f"합성 환자 {workflow['patient_count']}명에게 같은 시험과 처음 환자 자료를 주고, 추가 정보를 확인하지 않는 경우와 두 가지 확인 순서를 비교했다. 추가 확인을 사용하는 두 방법에는 환자 한 명당 최대 {workflow['action_budget']}번의 기회를 줬다.",
                "",
                "### 판단 결과",
                "",
                "| 부족한 정보를 처리한 방법 | 후보 유지·제외와 현재 확정 상태를 모두 맞힌 비율 | 후보 유지·제외를 맞힌 비율 | 현재 자료로 확정 가능한지를 맞힌 비율 | 남겨야 할 시험을 잘못 제외한 수 | 처음 자료가 부족한데 확정한 수 | 질문 뒤 판단이 끝난 시험 수 |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        arm_labels = {
            "no_questions": "추가 정보를 확인하지 않고 처음 환자 자료만 사용",
            "fixed_order": f"처음 빠진 정보 목록에 적힌 순서대로 최대 {workflow['action_budget']}개를 확인",
            "clarifytrial": f"남은 {workflow['action_budget']}번 안에 가장 많은 시험 판단을 끝낼 정보를 매번 다시 계산",
        }
        for row in workflow["arm_metrics"]:
            sections.append(
                f"| {arm_labels.get(row['arm'], row['arm'])} | {row['trial_status_recovery']:.1%} | {row['candidate_status_accuracy']:.1%} | {row['confirmation_status_accuracy']:.1%} | {row['false_candidate_removals']}개 | {row['premature_initial_confirmations']}개 | 환자당 {row['mean_unresolved_to_resolved']:.2f}개 |"
            )
            for name in (
                "trial_status_recovery",
                "candidate_status_accuracy",
                "confirmation_status_accuracy",
                "false_candidate_removals",
                "premature_initial_confirmations",
                "mean_unresolved_to_resolved",
                "mean_action_count",
                "model_call_count",
                "total_tokens",
                "failed_patient_count",
            ):
                metric_rows.append(
                    {"section": "full_workflow", "arm": row["arm"], "metric": name, "value": row[name]}
                )
        sections.extend(
            [
                "",
                "### 실행량과 오류",
                "",
                "| 부족한 정보를 처리한 방법 | 합성 환자 수 | 환자 한 명당 실제로 확인한 정보 수 | 조건 판단·질문 작성 단계를 실행한 총횟수 | 외부 언어모델을 사용했다면 보낸·받은 전체 토큰 | 실행 오류가 난 환자 수 |",
                "|---|---:|---:|---:|---:|---:|",
                *[
                    f"| {arm_labels.get(row['arm'], row['arm'])} | {row['patient_count']} | {row['mean_action_count']:.2f}개 | {row['model_call_count']}회 | {row['total_tokens']:,} | {row['failed_patient_count']}명 |"
                    for row in workflow["arm_metrics"]
                ],
            ]
        )
        paired = workflow.get("paired_clarifytrial_vs_fixed")
        if isinstance(paired, dict):
            normalized["full_workflow_paired_comparison"] = paired
            sections.extend(
                [
                    "",
                    f"환자별로 두 질문 순서를 직접 비교하면, 가장 많은 시험 판단을 끝낼 정보를 다시 계산한 방법이 더 좋았던 환자는 {paired['clarifytrial_better_patient_count']}명, 같았던 환자는 {paired['equal_patient_count']}명, 더 낮았던 환자는 {paired['clarifytrial_worse_patient_count']}명이었다.",
                ]
            )
            for name in (
                "clarifytrial_better_patient_count",
                "equal_patient_count",
                "clarifytrial_worse_patient_count",
                "mean_recovery_difference",
                "two_sided_exact_sign_test_p",
            ):
                value = paired.get(name)
                if value is not None:
                    metric_rows.append(
                        {
                            "section": "full_workflow_paired_comparison",
                            "arm": "clarifytrial_vs_input_order",
                            "metric": name,
                            "value": value,
                        }
                    )
        workflow_total_tokens = sum(row["total_tokens"] for row in workflow["arm_metrics"])
        if workflow_total_tokens == 0:
            workflow_execution_note = (
                "이 실행에서는 외부 언어모델을 부르지 않고, 합성 환자를 만들 때 저장한 "
                "답만 반환하는 실험용 코드를 사용했다. 표의 단계 실행 횟수는 조건 판단과 "
                "질문 작성 과정이 몇 번 작동했는지를 센 값이다. 외부 모델을 쓰지 않았으므로 "
                "토큰 사용량은 0이다."
            )
        else:
            workflow_execution_note = (
                "이 실행은 외부 언어모델을 사용했다. 표의 단계 실행 횟수는 조건 판단과 "
                "질문 작성 과정이 몇 번 작동했는지를 센 값이며, 토큰 수는 실행 기록에 "
                "저장된 입력·출력 사용량의 합이다."
            )
        sections.extend(["", workflow_execution_note, ""])

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
        corpus_labels = {
            "trec_2021": "2021년 공개 임상시험 검색 평가자료(TREC)",
            "trec_2022": "2022년 공개 임상시험 검색 평가자료(TREC)",
        }
        sections.extend(
            [
                "## 수만 건의 임상시험 문서에서 관련 시험을 찾아오는 검색 결과",
                "",
                "전문가가 환자와 관련 있다고 표시한 시험이 검색 결과 상위 500개 안에 들어왔는지를 공개 TREC 평가자료에서 확인했다. 이 결과는 검색 단계만 측정하며 참가 조건 판단은 포함하지 않는다.",
                "",
                "| 공개 평가자료 | 검색 대상 임상시험 문서 수 | 전문가가 관련 있다고 표시한 시험을 검색 결과 상위 500개 안에 남긴 비율 |",
                "|---|---:|---:|",
                *[
                    f"| {corpus_labels.get(item['corpus'], item['corpus'])} | {item['documents']:,} | {item['weighted_recall']:.2%} |"
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
