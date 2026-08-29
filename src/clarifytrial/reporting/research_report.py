"""Build tables, SVG figures, and Markdown directly from evaluation JSON."""

from __future__ import annotations

import csv
import json
import shutil
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
    budget_frontier_path: str | Path | None = None,
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
        "평가 대상은 부족한 환자 정보의 확인 순서, 환자의 이동·비용·시간 제한에 따른 확인 방법, 질문 뒤 끝난 임상시험 판단이다.",
        "모든 수치는 실행이 끝난 뒤 저장된 결과 파일에서 계산했다.",
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
            policy_id="clarifytrial_rule_v1",
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
                        "현재 미정인 시험과 가장 많이 연결된\n정보부터 확인",
                        current["trial_status_recovery"],
                    ),
                ],
            ),
            encoding="utf-8",
        )
        sections.extend(
            [
                "## 질문 순서 계산만 분리해 검사한 결과",
                "",
                f"처음 자료가 불완전한 합성 환자 {current['patient_count']}명에게 추가 확인을 최대 {action_budget}번 허용했다. 이 검사는 저장된 조건 연결과 정답 상태를 사용해 질문 순서 계산만 실행한다. 조건 판단 모델과 전체 에이전트 흐름은 실행하지 않으므로, 뒤에 나오는 연결 실행 결과와 수치를 합치지 않는다.",
                "",
                "| 부족한 정보를 고른 방법 | 확인 횟수 안에 판단을 끝낸 시험 비율 | 최종 판단에 실제로 필요했던 정보 중 확인한 비율 | 확인했지만 어떤 시험의 판단도 더 바꾸지 못한 정보 수 |",
                "|---|---:|---:|---:|",
                f"| 처음 빠진 정보 목록의 앞 {action_budget}개를 적힌 순서대로 확인 | {fixed['trial_status_recovery']:.1%} | {fixed['mean_needed_fact_recall']:.1%} | 환자당 {fixed['mean_unnecessary_action_count']:.2f}개 |",
                f"| 현재 미정인 시험과 가장 많이 연결된 정보부터 확인 | {current['trial_status_recovery']:.1%} | {current['mean_needed_fact_recall']:.1%} | 환자당 {current['mean_unnecessary_action_count']:.2f}개 |",
                "",
                "![질문 순서 결과](question-policy.svg)",
                "",
            ]
        )

    if burden_path is not None:
        burden = _read(burden_path)
        mechanism = burden.get("mechanism_ablation")
        hard_filter = (
            mechanism.get("disallowed_path_filter")
            if isinstance(mechanism, dict)
            else None
        )
        if isinstance(hard_filter, dict):
            normalized["patient_burden"] = {
                "evaluation": "disallowed_path_filter",
                **hard_filter,
            }
            means = hard_filter["metric_means"]
            totals = hard_filter["metric_totals"]
            feasible = means["burden_feasible_trial_status_recovery"]
            inference = hard_filter["paired_inference"][
                "burden_feasible_trial_status_recovery"
            ]
            interval = inference["bootstrap_95_ci"]
            current_metrics = {
                "feasible_status_match_without_limits": feasible["baseline"],
                "feasible_status_match_with_limits": feasible["candidate"],
                "new_tests_without_limits": totals["new_test_count"]["baseline"],
                "new_tests_with_limits": totals["new_test_count"]["candidate"],
                "visits_without_limits": totals["additional_visit_count"]["baseline"],
                "visits_with_limits": totals["additional_visit_count"]["candidate"],
                "limit_violations_without_limits": totals[
                    "explicit_limit_violations"
                ]["baseline"],
                "limit_violations_with_limits": totals[
                    "explicit_limit_violations"
                ]["candidate"],
            }
            for name, value in current_metrics.items():
                metric_rows.append(
                    {
                        "section": "patient_burden",
                        "arm": "explicit_patient_limits",
                        "metric": name,
                        "value": value,
                    }
                )
            (output / "patient-burden.svg").write_text(
                _bar_svg(
                    "환자가 이용할 수 있는 정보만 기준으로 맞힌 시험 상태",
                    (
                        f"합성 환자 {hard_filter['base_patient_count']}명 · "
                        f"같은 환자의 정보 가림과 확인 경로를 달리한 "
                        f"{hard_filter['setting_pair_count']}개 비교"
                    ),
                    [
                        (
                            "환자가 피하고 싶은 확인 방법을\n그대로 선택할 수 있게 둔 경우",
                            feasible["baseline"],
                        ),
                        (
                            "환자가 피하고 싶은 확인 방법을\n먼저 제외한 경우",
                            feasible["candidate"],
                        ),
                    ],
                ),
                encoding="utf-8",
            )
            sections.extend(
                [
                    "## 환자가 피하고 싶은 검사와 방문을 확인 후보에서 제외",
                    "",
                    (
                        f"합성 환자 {hard_filter['base_patient_count']}명에게서 정보 가림과 "
                        f"확인 경로를 달리한 {hard_filter['setting_pair_count']}개 설정을 만들었다. "
                        "두 경우에 같은 정보 영향 계산을 적용했다. 한쪽은 모든 확인 방법을 "
                        "선택 목록에 두었고, 다른 쪽은 환자가 피하고 싶다고 입력한 새 검사와 "
                        "추가 방문을 선택 전에 제외했다. 경로가 빠지면 다음 정보 순서도 달라질 "
                        "수 있다."
                    ),
                    "",
                    "| 결과 | 환자 제한을 적용하지 않음 | 환자가 피하고 싶은 방법을 제외 |",
                    "|---|---:|---:|",
                    f"| 환자에게 실제로 이용 가능한 정보만 보았을 때 기대했던 시험 상태와 일치 | {feasible['baseline']:.1%} | {feasible['candidate']:.1%} |",
                    f"| 새 검사를 선택한 횟수 | {totals['new_test_count']['baseline']}회 | {totals['new_test_count']['candidate']}회 |",
                    f"| 추가 방문을 선택한 횟수 | {totals['additional_visit_count']['baseline']}회 | {totals['additional_visit_count']['candidate']}회 |",
                    f"| 환자가 피하고 싶다고 입력한 방법을 선택한 횟수 | {totals['explicit_limit_violations']['baseline']}회 | {totals['explicit_limit_violations']['candidate']}회 |",
                    f"| 한 설정에서 선택한 확인 방법의 예상 대기시간 합계 | {means['cumulative_delay_hours']['baseline']:.2f}시간 | {means['cumulative_delay_hours']['candidate']:.2f}시간 |",
                    "",
                    (
                        "환자에게 실제로 이용 가능한 정보만 기준으로 삼은 시험 상태 일치는 "
                        f"{feasible['difference']:+.1%}p 높았다. 환자 {inference['pair_count']}명을 "
                        "단위로 다시 뽑아 계산한 95% 범위는 "
                        f"{interval['lower']:+.1%}p에서 {interval['upper']:+.1%}p였다. "
                        "새 검사와 추가 방문을 피한 만큼 예상 대기시간이 늘어난 점도 함께 "
                        "표시했다."
                    ),
                    "",
                    "![환자 부담 결과](patient-burden.svg)",
                    "",
                ]
            )
        else:
            comparisons = burden["adoption_comparison"]
            if split not in comparisons:
                raise ValueError(
                    f"burden result has no requested split {split!r}"
                )
            comparison = comparisons[split]
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
                    {"section": "patient_burden", "arm": split, "metric": name, "value": value}
                )
            (output / "patient-burden.svg").write_text(
                _bar_svg(
                    "환자 제한을 지키면서 최종 판단까지 끝낸 시험",
                    "이동·비용 제한이 있거나 새 검사·추가 방문을 피해야 하는 합성 상황",
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
                    f"| 이동·비용 제한이 있는 합성 환자에게 허용된 확인 방법만 사용하고도 최종 판단까지 끝낸 시험 비율 | {comparison['constrained_baseline_feasible_recovery']:.1%} | {comparison['constrained_candidate_feasible_recovery']:.1%} |",
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
        broad_search = workflow.get("broad_search_metrics") or {}
        if broad_search:
            workflow_scope = (
                f"모집 중 시험 {broad_search['corpus_trial_count']}건에서, 미리 정한 "
                f"서로 다른 평가 시험 {broad_search.get('unique_target_trial_count')}건을 "
                f"환자별로 반복한 {broad_search.get('target_patient_trial_count')}개 연결을 "
                f"모두 상위 {broad_search['top_k']}개 안에서 찾았다. 검색된 다른 시험은 "
                "조건 판정에 넣지 않았으므로 이 부분은 검색 연결 검사다."
            )
            for name in (
                "target_recall",
                "mean_retrieved_target_rank",
                "worst_retrieved_target_rank",
            ):
                metric_rows.append(
                    {
                        "section": "broad_search",
                        "arm": broad_search["retrieval_method"],
                        "metric": name,
                        "value": broad_search[name],
                    }
                )
        else:
            workflow_scope = (
                "질환별 후보 시험 5개를 미리 정한 뒤 조건 판정과 질문 뒤 "
                "재판정을 실행했다. 수천 건에서 후보를 찾는 검색 단계는 "
                "이 결과에 포함하지 않는다."
            )
        workflow_heading = (
            "## 검색부터 질문 뒤 재판정까지 전체 프로그램을 점검한 결과"
            if workflow.get("model") == "deterministic-workflow"
            else "## 외부 모델을 사용한 질문 뒤 재판정 결과"
        )
        sections.extend(
            [
                workflow_heading,
                "",
                f"합성 환자 {workflow['patient_count']}명에게 같은 처음 환자 자료를 주고, 추가 정보를 확인하지 않는 경우와 세 가지 확인 순서를 비교했다. 추가 확인을 사용하는 방법에는 환자 한 명당 최대 {workflow['action_budget']}번의 기회를 줬다. {workflow_scope}",
                "",
                "### 판단 결과",
                "",
                "| 부족한 정보를 처리한 방법 | 후보 유지·제외와 현재 확정 상태를 모두 맞힌 비율 | 후보 유지·제외를 맞힌 비율 | 현재 자료로 확정 가능한지를 맞힌 비율 | 남겨야 할 시험을 잘못 제외한 수 | 질문 뒤에도 정보가 부족한데 확정한 수 | 환자당 질문 뒤 판단이 끝난 시험 수 |",
                "|---|---:|---:|---:|---:|---:|---:|",
            ]
        )
        arm_labels = {
            "no_questions": "추가 정보를 확인하지 않고 처음 환자 자료만 사용",
            "fixed_order": f"처음 빠진 정보 목록에 적힌 순서대로 최대 {workflow['action_budget']}개를 확인",
            "immediate_coverage": f"환자 상황을 적용하지 않고 여러 시험에 함께 필요한 정보를 최대 {workflow['action_budget']}개 확인",
            "clarifytrial": f"여러 시험에 함께 필요한 정보를 먼저 고르고 환자가 이용할 수 있는 방법으로 최대 {workflow['action_budget']}개 확인",
        }
        for row in workflow["arm_metrics"]:
            sections.append(
                f"| {arm_labels.get(row['arm'], row['arm'])} | {row['trial_status_recovery']:.1%} | {row['candidate_status_accuracy']:.1%} | {row['confirmation_status_accuracy']:.1%} | {row['false_candidate_removals']}개 | {row.get('premature_final_confirmations', 0)}개 | 환자당 {row['mean_unresolved_to_resolved']:.2f}개 |"
            )
            for name in (
                "trial_status_recovery",
                "candidate_status_accuracy",
                "confirmation_status_accuracy",
                "false_candidate_removals",
                "premature_initial_confirmations",
                "premature_final_confirmations",
                "resolved_to_unresolved",
                "mean_unresolved_to_resolved",
                "mean_action_count",
                "model_call_count",
                "total_tokens",
                "failed_patient_count",
            ):
                metric_rows.append(
                    {"section": "full_workflow", "arm": row["arm"], "metric": name, "value": row[name]}
                )
        if all(
            "rescue_opportunity_count" in row
            for row in workflow["arm_metrics"]
        ):
            sections.extend(
                [
                    "",
                    "### 처음에는 보이지 않던 실제 후보를 추가 확인으로 확정한 결과",
                    "",
                    "처음 화면에서 현재 확인이 끝난 시험만 보여 주면 보이지 않지만, 가상 환자의 숨겨 둔 전체 상태에서는 참가 가능한 시험을 따로 셌다. 반대로 처음에는 정보 부족으로 후보에 남았지만 전체 상태에서는 제외되는 시험도 함께 계산했다.",
                    "",
                    "| 부족한 정보를 처리한 방법 | 처음에는 보이지 않던 실제 후보 | 추가 확인 후보로 보존 | 질문 뒤 실제 후보로 확정 | 처음에는 정보 부족으로 남은 부적합 후보 | 질문 뒤 제외 | 새 검사·추가 방문 |",
                    "|---|---:|---:|---:|---:|---:|---:|",
                ]
            )
            for row in workflow["arm_metrics"]:
                rescue_rate = row["confirmed_rescue_rate"]
                false_resolution_rate = row["false_preservation_resolution_rate"]
                rescue_rate_text = (
                    "—" if rescue_rate is None else f"{rescue_rate:.1%}"
                )
                false_rate_text = (
                    "—"
                    if false_resolution_rate is None
                    else f"{false_resolution_rate:.1%}"
                )
                sections.append(
                    f"| {arm_labels.get(row['arm'], row['arm'])} | "
                    f"{row['rescue_opportunity_count']}개 | "
                    f"{row['candidate_preservation_count']}개 | "
                    f"{row['confirmed_rescue_count']}개 "
                    f"({rescue_rate_text}) | "
                    f"{row['false_preservation_count']}개 | "
                    f"{row['false_preservation_resolved_count']}개 "
                    f"({false_rate_text}) | "
                    f"{row.get('new_test_count', 0)}회·"
                    f"{row.get('additional_visit_count', 0)}회 |"
                )
                for name in (
                    "rescue_opportunity_count",
                    "candidate_preservation_count",
                    "confirmed_rescue_count",
                    "confirmed_rescue_rate",
                    "false_preservation_count",
                    "false_preservation_resolved_count",
                    "false_preservation_resolution_rate",
                    "new_test_count",
                    "additional_visit_count",
                ):
                    metric_rows.append(
                        {
                            "section": "candidate_rescue",
                            "arm": row["arm"],
                            "metric": name,
                            "value": row.get(name, 0),
                        }
                    )
            current_rescue = next(
                row
                for row in workflow["arm_metrics"]
                if row["arm"] == "clarifytrial"
            )
            sections.extend(
                [
                    "",
                    f"현재 확인이 끝난 시험만 보여 주면 사라질 실제 참가 가능 후보 "
                    f"{current_rescue['rescue_opportunity_count']}개를 모두 추가 확인 후보로 "
                    f"남겼고, 정해진 확인 횟수 안에 "
                    f"{current_rescue['confirmed_rescue_count']}개를 실제 후보로 확정했다.",
                ]
            )
            uncertainty = current_rescue.get("cluster_uncertainty")
            if isinstance(uncertainty, dict):
                uncertainty_rows = [
                    (
                        "최종 상태 일치",
                        current_rescue["trial_status_recovery"],
                        uncertainty.get("trial_status_recovery"),
                    ),
                    (
                        "실제 후보 확정",
                        current_rescue["confirmed_rescue_rate"],
                        uncertainty.get("confirmed_rescue_rate"),
                    ),
                    (
                        "제외 후보 정리",
                        current_rescue["false_preservation_resolution_rate"],
                        uncertainty.get("false_preservation_resolution_rate"),
                    ),
                ]
                if all(isinstance(item[2], dict) for item in uncertainty_rows):
                    sections.extend(
                        [
                            "",
                            "### 같은 환자에서 나온 시험 판단을 묶어 본 결과 범위",
                            "",
                            "한 환자에게 연결된 여러 시험을 서로 독립인 사례로 세지 않았다. 환자를 단위로 2,000번 다시 뽑아 95% 범위를 계산하고 질환별 결과 범위도 함께 표시했다.",
                            "",
                            "| 지표 | 결과 | 환자 단위 95% 범위 | 질환별 범위 |",
                            "|---|---:|---:|---:|",
                        ]
                    )
                    for label, value, detail in uncertainty_rows:
                        interval = detail["bootstrap_95_ci"]
                        group_range = detail["disease_group_rate_range"]
                        sections.append(
                            f"| {label} | {value:.1%} | "
                            f"{interval['lower']:.1%}~{interval['upper']:.1%} | "
                            f"{group_range['minimum']:.1%}~{group_range['maximum']:.1%} |"
                        )
                        metric_rows.extend(
                            [
                                {
                                    "section": "patient_cluster_uncertainty",
                                    "arm": "clarifytrial",
                                    "metric": f"{label}_bootstrap_95_lower",
                                    "value": interval["lower"],
                                },
                                {
                                    "section": "patient_cluster_uncertainty",
                                    "arm": "clarifytrial",
                                    "metric": f"{label}_bootstrap_95_upper",
                                    "value": interval["upper"],
                                },
                            ]
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
                    f"환자별로 두 질문 순서를 직접 비교하면, 여러 시험에 함께 필요한 정보를 먼저 확인한 방법이 더 좋았던 환자는 {paired['clarifytrial_better_patient_count']}명, 같았던 환자는 {paired['equal_patient_count']}명, 더 낮았던 환자는 {paired['clarifytrial_worse_patient_count']}명이었다.",
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
        immediate_paired = workflow.get(
            "paired_clarifytrial_vs_immediate_coverage"
        )
        if isinstance(immediate_paired, dict):
            sections.extend(
                [
                    "",
                    "환자 상황을 적용하지 않고 여러 시험에 함께 필요한 정보부터 확인한 방식과 비교하면, "
                    f"환자가 이용할 수 있는 확인 방법까지 반영한 방식이 더 좋았던 환자는 {immediate_paired['clarifytrial_better_patient_count']}명, "
                    f"같았던 환자는 {immediate_paired['equal_patient_count']}명, "
                    f"더 낮았던 환자는 {immediate_paired['clarifytrial_worse_patient_count']}명이었다.",
                ]
            )
        separation = workflow.get("decision_separation")
        if isinstance(separation, dict):
            sections.extend(
                [
                    "",
                    "### 후보 유지와 현재 확인을 따로 표시해야 하는 사례",
                    "",
                    f"처음 자료에서 후보로는 남겨야 하지만 아직 참가 조건을 확인할 수 없었던 시험 판단은 {separation['retained_but_not_confirmed_count']}개였다. 하나의 답만 사용해 확인이 끝난 시험만 남기면 이 후보들을 모두 잃고, 반대로 남긴 시험을 모두 확인 완료로 표시하면 같은 수만큼 성급하게 확정하게 된다.",
                ]
            )
        group_metrics = workflow.get("group_metrics")
        if isinstance(group_metrics, list) and group_metrics:
            group_labels = {
                "type_2_diabetes": "제2형 당뇨병",
                "breast_cancer": "유방암",
                "major_depressive_disorder": "주요우울장애",
            }
            rows_by_group = {}
            for row in group_metrics:
                rows_by_group.setdefault(row["group_id"], {})[row["arm"]] = row
            sections.extend(
                [
                    "",
                    "### 질환별 판단 완료",
                    "",
                    "| 합성 환자 질환 | 환자 상황을 적용하지 않고 여러 시험에 함께 필요한 정보부터 확인 | 같은 정보 순서를 쓰되 환자가 이용할 수 있는 확인 방법을 적용 |",
                    "|---|---:|---:|",
                ]
            )
            for group_id, rows_for_group in sorted(rows_by_group.items()):
                immediate = rows_for_group.get("immediate_coverage")
                current = rows_for_group.get("clarifytrial")
                if immediate is None or current is None:
                    continue
                reported_label = current.get("group_label")
                group_label = (
                    reported_label
                    if reported_label and reported_label != group_id
                    else group_labels.get(group_id, group_id)
                )
                sections.append(
                    f"| {group_label} | "
                    f"{immediate['trial_status_recovery']:.1%} | "
                    f"{current['trial_status_recovery']:.1%} |"
                )
        unavailable_metrics = workflow.get("unavailable_answer_metrics")
        if isinstance(unavailable_metrics, list) and unavailable_metrics:
            sections.extend(
                [
                    "",
                    "### 확인하려던 정보를 얻지 못한 경우",
                    "",
                    "환자마다 정해 둔 답 하나를 제공하지 않고 같은 흐름을 다시 실행했다.",
                    "",
                    "| 부족한 정보를 처리한 방법 | 판단을 끝낸 시험 비율 | 정보를 얻지 못한 확인 | 같은 정보 반복 |",
                    "|---|---:|---:|---:|",
                ]
            )
            for row in unavailable_metrics:
                if row["arm"] == "no_questions":
                    continue
                sections.append(
                    f"| {arm_labels.get(row['arm'], row['arm'])} | "
                    f"{row['trial_status_recovery']:.1%} | "
                    f"{row['unavailable_action_count']}회 | "
                    f"{row['repeated_fact_action_count']}회 |"
                )
                for name in (
                    "trial_status_recovery",
                    "unavailable_action_count",
                    "repeated_fact_action_count",
                ):
                    metric_rows.append(
                        {
                            "section": "unavailable_answer",
                            "arm": row["arm"],
                            "metric": name,
                            "value": row[name],
                        }
                    )
            sections.extend(
                [
                    "",
                    "답을 얻지 못하면 판단 완료율은 낮아지지만, 현재 실행에서는 얻지 못한 같은 정보를 다시 확인하지 않고 남은 정보로 넘어갔다.",
                ]
            )
        declined_metrics = workflow.get("patient_declines_new_tests_metrics")
        if isinstance(declined_metrics, list) and declined_metrics:
            normal_current = next(
                row
                for row in workflow["arm_metrics"]
                if row["arm"] == "clarifytrial"
            )
            declined_current = next(
                row for row in declined_metrics if row["arm"] == "clarifytrial"
            )
            sections.extend(
                [
                    "",
                    "### 환자가 새 검사와 추가 방문을 원하지 않은 경우",
                    "",
                    "같은 합성 환자에게 새 검사와 추가 방문을 허용한 경우와 허용하지 않은 경우를 따로 실행했다.",
                    "",
                    "| 환자 선택 | 실제 후보로 확정 | 결국 제외될 후보를 정리 | 새 검사 | 추가 방문 |",
                    "|---|---:|---:|---:|---:|",
                    f"| 새 검사를 허용 | {normal_current['confirmed_rescue_count']}/{normal_current['rescue_opportunity_count']}개 | {normal_current['false_preservation_resolved_count']}/{normal_current['false_preservation_count']}개 | {normal_current['new_test_count']}회 | {normal_current['additional_visit_count']}회 |",
                    f"| 새 검사와 추가 방문을 거절 | {declined_current['confirmed_rescue_count']}/{declined_current['rescue_opportunity_count']}개 | {declined_current['false_preservation_resolved_count']}/{declined_current['false_preservation_count']}개 | {declined_current['new_test_count']}회 | {declined_current['additional_visit_count']}회 |",
                    "",
                    "환자가 허용하지 않은 확인 방법으로 실제 후보 확정 수를 높이지 않는다. 확인하지 못한 정보가 남아 확정 수가 낮아질 수 있으므로 피한 검사와 방문을 함께 표시한다.",
                ]
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
            correction_count = sum(
                row.get("mechanical_model_correction_count", 0)
                for row in workflow["arm_metrics"]
            )
            workflow_execution_note = (
                "이 실행은 외부 언어모델을 사용했다. 표의 단계 실행 횟수는 조건 판단과 "
                "질문 작성 과정이 몇 번 작동했는지를 센 값이며, 토큰 수는 실행 기록에 "
                f"저장된 입력·출력 사용량의 합이다. 구조화된 수치·날짜 규칙과 다른 모델 "
                f"판단은 코드가 {correction_count}건 바로잡았다."
            )
        sections.extend(["", workflow_execution_note, ""])

    if budget_frontier_path is not None:
        frontier_source = Path(budget_frontier_path)
        frontier_file = (
            frontier_source / "frontier.json"
            if frontier_source.is_dir()
            else frontier_source
        )
        frontier = _read(frontier_file)
        normalized["budget_frontier"] = frontier
        declared_budgets = sorted(
            {int(row["action_budget"]) for row in frontier["rows"]}
        )
        patient_label = (
            f"같은 합성 환자 {frontier['patient_count']}명에게"
            if frontier.get("patient_count") is not None
            else "같은 평가 사례에"
        )
        source_dir = frontier_file.parent
        for name in (
            "candidate-rescue-by-budget.svg",
            "false-preservation-cleanup-by-budget.svg",
        ):
            source = source_dir / name
            if not source.is_file():
                raise ValueError(f"budget frontier is missing figure: {source}")
            shutil.copyfile(source, output / name)
        frontier_labels = {
            "fixed_order": "입력 파일에 적힌 순서",
            "immediate_coverage": "여러 시험에 함께 필요한 정보 우선, 환자 상황 미반영",
            "clarifytrial": "여러 시험에 함께 필요한 정보 우선, 환자 상황 반영",
        }
        sections.extend(
            [
                "## 확인 횟수를 늘렸을 때 후보 확정과 제외 정리가 어떻게 바뀌는가",
                "",
                f"{patient_label} 확인 기회를 {declared_budgets[0]}회부터 {declared_budgets[-1]}회까지 차례로 늘렸다. 실제 참가 가능 후보를 확정한 비율과, 처음에는 남았지만 결국 제외되는 후보를 정리한 비율을 따로 계산했다.",
                "",
                "| 확인 가능 횟수 | 정보를 고른 방법 | 실제 참가 가능 후보로 확정 | 결국 제외될 후보 정리 | 전체 시험 판단 일치 |",
                "|---:|---|---:|---:|---:|",
            ]
        )
        for row in frontier["rows"]:
            if row["arm"] not in frontier_labels:
                continue
            sections.append(
                f"| {row['action_budget']} | {frontier_labels[row['arm']]} | "
                f"{row['confirmed_rescue_rate']:.1%} | "
                f"{row['false_preservation_resolution_rate']:.1%} | "
                f"{row['trial_status_recovery']:.1%} |"
            )
            for name in (
                "confirmed_rescue_rate",
                "false_preservation_resolution_rate",
                "trial_status_recovery",
            ):
                metric_rows.append(
                    {
                        "section": "budget_frontier",
                        "arm": row["arm"],
                        "metric": f"budget_{row['action_budget']}_{name}",
                        "value": row[name],
                    }
                )
        tight = frontier.get("tight_budget_comparison")
        if isinstance(tight, dict):
            paired_fixed = tight.get("paired_clarifytrial_vs_fixed")
            sections.extend(
                [
                    "",
                    f"### 확인 기회가 {tight['action_budget']}번뿐일 때",
                    "",
                    (
                        "질문 순서의 차이가 가장 잘 드러나는 제한된 상황을 따로 봤다. "
                        "입력 파일에 적힌 순서와, 여러 시험에 영향을 주는 정보를 먼저 "
                        "확인하는 방법을 같은 환자에서 비교했다."
                    ),
                    "",
                    "| 정보를 고른 방법 | 전체 시험 판단 일치 | 추가 정보 한 건당 보류 상태를 끝낸 시험 |",
                    "|---|---:|---:|",
                    f"| 입력 파일에 적힌 순서 | {tight['baseline_trial_status_recovery']:.1%} | {tight['baseline_resolved_trials_per_action']:.2f}개 |",
                    f"| 여러 시험에 영향을 주는 정보 우선 | {tight['clarifytrial_trial_status_recovery']:.1%} | {tight['clarifytrial_resolved_trials_per_action']:.2f}개 |",
                ]
            )
            metric_rows.extend(
                [
                    {
                        "section": "tight_budget_comparison",
                        "arm": "fixed_order",
                        "metric": "trial_status_recovery",
                        "value": tight["baseline_trial_status_recovery"],
                    },
                    {
                        "section": "tight_budget_comparison",
                        "arm": "clarifytrial",
                        "metric": "trial_status_recovery",
                        "value": tight["clarifytrial_trial_status_recovery"],
                    },
                    {
                        "section": "tight_budget_comparison",
                        "arm": "fixed_order",
                        "metric": "resolved_trials_per_action",
                        "value": tight["baseline_resolved_trials_per_action"],
                    },
                    {
                        "section": "tight_budget_comparison",
                        "arm": "clarifytrial",
                        "metric": "resolved_trials_per_action",
                        "value": tight["clarifytrial_resolved_trials_per_action"],
                    },
                ]
            )
            if isinstance(paired_fixed, dict):
                sections.extend(
                    [
                        "",
                        (
                            f"환자 {paired_fixed['patient_count']}명 중 여러 시험에 영향을 주는 "
                            f"정보를 먼저 확인한 방법이 더 좋았던 환자는 "
                            f"{paired_fixed['clarifytrial_better_patient_count']}명, 같았던 환자는 "
                            f"{paired_fixed['equal_patient_count']}명, 더 낮았던 환자는 "
                            f"{paired_fixed['clarifytrial_worse_patient_count']}명이었다. 차이가 없는 "
                            "환자를 제외한 양측 정확 부호 검정의 p값은 "
                            f"{paired_fixed['two_sided_exact_sign_test_p']:.6f}이었다."
                        ),
                    ]
                )
                metric_rows.append(
                    {
                        "section": "tight_budget_comparison",
                        "arm": "clarifytrial_vs_fixed_order",
                        "metric": "two_sided_exact_sign_test_p",
                        "value": paired_fixed["two_sided_exact_sign_test_p"],
                    }
                )
            paired_immediate = tight.get(
                "paired_clarifytrial_vs_immediate_coverage"
            )
            if (
                isinstance(paired_immediate, dict)
                and paired_immediate["clarifytrial_better_patient_count"] == 0
                and paired_immediate["clarifytrial_worse_patient_count"] == 0
            ):
                sections.extend(
                    [
                        "",
                        "이 조건에서는 환자 상황을 적용한 경우와 적용하지 않은 경우의 최종 판단 결과가 같았다.",
                    ]
                )
        sections.extend(
            [
                "",
                "후보 확정과 제외 정리는 서로 다른 목적이다. 한 비율만 높이면 다른 쪽이 늦어질 수 있으므로 두 결과를 합쳐 한 점수로 만들지 않았다.",
                "",
                "![확인 횟수별 실제 후보 확정](candidate-rescue-by-budget.svg)",
                "",
                "![확인 횟수별 제외 후보 정리](false-preservation-cleanup-by-budget.svg)",
                "",
            ]
        )

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
