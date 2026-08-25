"""Summarize candidate rescue and cleanup across information-check budgets."""

from __future__ import annotations

import csv
import json
from math import sqrt
from pathlib import Path
from typing import Any

from ..io import atomic_write_text


_ARM_LABELS = {
    "no_questions": "추가 확인 없음",
    "fixed_order": "입력 파일에 적힌 순서",
    "immediate_coverage": "현재 가장 많은 시험에 연결된 정보 우선",
    "clarifytrial": "남은 확인 횟수 전체를 고려",
}
_COLORS = {
    "fixed_order": "#8B95A1",
    "immediate_coverage": "#4F6B95",
    "clarifytrial": "#173B63",
}


def _read(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _wilson(successes: int, total: int) -> list[float] | None:
    if total <= 0:
        return None
    z = 1.959963984540054
    proportion = successes / total
    denominator = 1 + z * z / total
    center = (proportion + z * z / (2 * total)) / denominator
    half = (
        z
        * sqrt(proportion * (1 - proportion) / total + z * z / (4 * total * total))
        / denominator
    )
    return [max(0.0, center - half), min(1.0, center + half)]


def _auc(rows: list[dict[str, Any]], metric: str) -> float | None:
    points = sorted(
        (int(row["action_budget"]), row.get(metric))
        for row in rows
        if row.get(metric) is not None
    )
    if len(points) < 2 or points[-1][0] == points[0][0]:
        return None
    area = sum(
        (right_budget - left_budget) * (left_value + right_value) / 2
        for (left_budget, left_value), (right_budget, right_value) in zip(
            points,
            points[1:],
        )
    )
    return area / (points[-1][0] - points[0][0])


def _line_svg(
    *,
    title: str,
    subtitle: str,
    rows: list[dict[str, Any]],
    metric: str,
) -> str:
    width = 1040
    height = 590
    left = 90
    right = 38
    top = 105
    bottom = 80
    chart_width = width - left - right
    chart_height = height - top - bottom
    budgets = sorted({int(row["action_budget"]) for row in rows})
    maximum_budget = max(budgets) if budgets else 1

    def x(value: int) -> float:
        return left + (value / max(1, maximum_budget)) * chart_width

    def y(value: float) -> float:
        return top + (1 - value) * chart_height

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" rx="18" fill="#F7F9FC"/>',
        f'<text x="{left}" y="42" font-family="Pretendard, Noto Sans KR, Malgun Gothic, Arial, sans-serif" font-size="25" font-weight="700" fill="#152536">{title}</text>',
        f'<text x="{left}" y="70" font-family="Pretendard, Noto Sans KR, Malgun Gothic, Arial, sans-serif" font-size="15" fill="#52606D">{subtitle}</text>',
    ]
    for tick in (0.0, 0.25, 0.5, 0.75, 1.0):
        yy = y(tick)
        parts.extend(
            [
                f'<line x1="{left}" y1="{yy:.1f}" x2="{width-right}" y2="{yy:.1f}" stroke="#DCE3EA" stroke-width="1"/>',
                f'<text x="{left-14}" y="{yy+5:.1f}" text-anchor="end" font-family="Pretendard, Noto Sans KR, Malgun Gothic, Arial, sans-serif" font-size="13" fill="#697887">{tick:.0%}</text>',
            ]
        )
    for budget in budgets:
        xx = x(budget)
        parts.extend(
            [
                f'<line x1="{xx:.1f}" y1="{top}" x2="{xx:.1f}" y2="{height-bottom}" stroke="#EDF1F5" stroke-width="1"/>',
                f'<text x="{xx:.1f}" y="{height-bottom+30}" text-anchor="middle" font-family="Pretendard, Noto Sans KR, Malgun Gothic, Arial, sans-serif" font-size="14" fill="#52606D">{budget}</text>',
            ]
        )
    parts.append(
        f'<text x="{left+chart_width/2:.1f}" y="{height-22}" text-anchor="middle" font-family="Pretendard, Noto Sans KR, Malgun Gothic, Arial, sans-serif" font-size="15" fill="#34495E">확인할 수 있는 정보 수</text>'
    )
    legend_x = left
    for arm in ("fixed_order", "immediate_coverage", "clarifytrial"):
        arm_rows = sorted(
            [row for row in rows if row["arm"] == arm],
            key=lambda row: int(row["action_budget"]),
        )
        points = [
            (x(int(row["action_budget"])), y(float(row[metric])))
            for row in arm_rows
            if row.get(metric) is not None
        ]
        if not points:
            continue
        color = _COLORS[arm]
        parts.append(
            f'<polyline points="{" ".join(f"{xx:.1f},{yy:.1f}" for xx, yy in points)}" fill="none" stroke="{color}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>'
        )
        for xx, yy in points:
            parts.append(
                f'<circle cx="{xx:.1f}" cy="{yy:.1f}" r="5" fill="#F7F9FC" stroke="{color}" stroke-width="3"/>'
            )
        parts.extend(
            [
                f'<line x1="{legend_x}" y1="{height-52}" x2="{legend_x+26}" y2="{height-52}" stroke="{color}" stroke-width="4"/>',
                f'<text x="{legend_x+34}" y="{height-47}" font-family="Pretendard, Noto Sans KR, Malgun Gothic, Arial, sans-serif" font-size="13" fill="#34495E">{_ARM_LABELS[arm]}</text>',
            ]
        )
        legend_x += 300
    parts.append("</svg>")
    return "\n".join(parts)


def build_budget_frontier(
    *,
    workflow_summary_paths: list[str | Path],
    output_dir: str | Path,
) -> dict[str, Any]:
    if len(workflow_summary_paths) < 2:
        raise ValueError("budget frontier needs at least two workflow summaries")
    summaries = [_read(path) for path in workflow_summary_paths]
    budgets = [int(item["action_budget"]) for item in summaries]
    if len(budgets) != len(set(budgets)):
        raise ValueError("workflow summaries repeat an action budget")
    first = summaries[0]
    for item in summaries[1:]:
        if item["model"] != first["model"] or item["split"] != first["split"]:
            raise ValueError("budget frontier summaries use different model or split")
        if item["patient_count"] != first["patient_count"]:
            raise ValueError("budget frontier summaries use different patient counts")

    rows = []
    for summary in summaries:
        budget = int(summary["action_budget"])
        for metrics in summary["arm_metrics"]:
            row = {"action_budget": budget, **metrics}
            row["confirmed_rescue_rate_ci95"] = _wilson(
                int(metrics.get("confirmed_rescue_count", 0)),
                int(metrics.get("rescue_opportunity_count", 0)),
            )
            row["false_preservation_resolution_rate_ci95"] = _wilson(
                int(metrics.get("false_preservation_resolved_count", 0)),
                int(metrics.get("false_preservation_count", 0)),
            )
            row["trial_status_recovery_ci95"] = _wilson(
                round(float(metrics["trial_status_recovery"]) * metrics["trial_count"]),
                int(metrics["trial_count"]),
            )
            rows.append(row)

    arm_summaries = []
    for arm in ("fixed_order", "immediate_coverage", "clarifytrial"):
        arm_rows = [item for item in rows if item["arm"] == arm]
        arm_summaries.append(
            {
                "arm": arm,
                "confirmed_rescue_rate_auc": _auc(
                    arm_rows, "confirmed_rescue_rate"
                ),
                "false_preservation_resolution_rate_auc": _auc(
                    arm_rows, "false_preservation_resolution_rate"
                ),
                "trial_status_recovery_auc": _auc(
                    arm_rows, "trial_status_recovery"
                ),
            }
        )

    payload = {
        "protocol_id": "clarifytrial-budget-frontier-v1",
        "model": first["model"],
        "split": first["split"],
        "patient_count": first["patient_count"],
        "budgets": sorted(budgets),
        "evaluation_scope": first.get("evaluation_scope", {}),
        "broad_search_metrics": first.get("broad_search_metrics"),
        "rows": sorted(rows, key=lambda row: (row["action_budget"], row["arm"])),
        "arm_summaries": arm_summaries,
    }
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        destination / "frontier.json",
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    with (destination / "frontier.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        fieldnames = [
            "action_budget",
            "arm",
            "patient_count",
            "trial_count",
            "confirmed_rescue_count",
            "rescue_opportunity_count",
            "confirmed_rescue_rate",
            "false_preservation_resolved_count",
            "false_preservation_count",
            "false_preservation_resolution_rate",
            "trial_status_recovery",
            "mean_action_count",
            "new_test_count",
            "additional_visit_count",
        ]
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(payload["rows"])

    atomic_write_text(
        destination / "candidate-rescue-by-budget.svg",
        _line_svg(
            title="확인 횟수에 따른 실제 후보 확정",
            subtitle="처음에는 추가 확인이 필요했지만 가상 환자 전체 상태에서는 참가 가능한 시험",
            rows=rows,
            metric="confirmed_rescue_rate",
        ),
    )
    atomic_write_text(
        destination / "false-preservation-cleanup-by-budget.svg",
        _line_svg(
            title="확인 횟수에 따른 부적합 후보 제외",
            subtitle="처음에는 후보로 남았지만 가상 환자 전체 상태에서는 제외되는 시험",
            rows=rows,
            metric="false_preservation_resolution_rate",
        ),
    )

    lines = [
        "# 확인 횟수에 따른 실제 후보 확정과 부적합 후보 제외",
        "",
        (
            "같은 환자와 시험에 확인 기회를 0회부터 늘려 가며, 실제 참가 가능 "
            "후보를 얼마나 확정했는지와 결국 제외될 후보를 얼마나 정리했는지를 "
            "함께 계산했다."
        ),
        "",
        "| 확인 가능 횟수 | 정보 선택 방법 | 실제 후보로 확정 | 결국 제외될 후보 정리 | 전체 시험 판단 일치 | 실제 사용한 확인 수 |",
        "|---:|---|---:|---:|---:|---:|",
    ]
    for row in payload["rows"]:
        if row["arm"] == "no_questions" and row["action_budget"] != min(budgets):
            continue
        rescue = row.get("confirmed_rescue_rate")
        cleanup = row.get("false_preservation_resolution_rate")
        lines.append(
            f"| {row['action_budget']} | {_ARM_LABELS.get(row['arm'], row['arm'])} | "
            f"{'해당 없음' if rescue is None else f'{rescue:.1%}'} | "
            f"{'해당 없음' if cleanup is None else f'{cleanup:.1%}'} | "
            f"{row['trial_status_recovery']:.1%} | {row['mean_action_count']:.2f}회 |"
        )
    lines.extend(
        [
            "",
            "두 비율은 서로 다른 목적을 나타낸다. 실제 후보 확정만 높이면 제외될 "
            "후보가 오래 남을 수 있고, 제외 정리만 높이면 참가 가능한 후보 확인이 "
            "늦어질 수 있다. 따라서 한 숫자로 합치지 않는다.",
            "",
            "![실제 후보 확정](candidate-rescue-by-budget.svg)",
            "",
            "![부적합 후보 제외](false-preservation-cleanup-by-budget.svg)",
            "",
        ]
    )
    atomic_write_text(destination / "frontier.md", "\n".join(lines))
    return payload


__all__ = ["build_budget_frontier"]
