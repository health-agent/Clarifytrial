"""Describe how patient facts are reused across trials in a benchmark.

This module measures only the structure of a selected benchmark.  It does not
estimate how common the facts are in clinical practice or how accurately a
screening system handles them.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence


PROXY_FACT_PATTERN = re.compile(r"^nct\d+_(?:source|logic)_line_\d+$")


def is_trial_specific_proxy_fact(fact_code: str) -> bool:
    """Return whether *fact_code* represents one source line in one trial."""

    return bool(PROXY_FACT_PATTERN.fullmatch(fact_code.strip().lower()))


def _required_sequence(document: Mapping[str, Any], key: str) -> Sequence[Mapping[str, Any]]:
    value = document.get(key)
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ValueError(f"trial set field {key!r} must be a list of objects")
    return value


def _required_text(item: Mapping[str, Any], key: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"record field {key!r} must be a non-empty string")
    return value.strip()


def _fact_rows(
    criteria: Sequence[Mapping[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    trial_ids_by_fact: dict[str, set[str]] = defaultdict(set)
    criterion_count_by_fact: dict[str, int] = defaultdict(int)

    for criterion in criteria:
        fact_code = _required_text(criterion, "fact_code")
        trial_id = _required_text(criterion, "nct_id")
        trial_ids_by_fact[fact_code].add(trial_id)
        criterion_count_by_fact[fact_code] += 1

    rows = []
    for fact_code in sorted(trial_ids_by_fact):
        trial_ids = sorted(trial_ids_by_fact[fact_code])
        rows.append(
            {
                "fact_code": fact_code,
                "fact_type": (
                    "trial_specific_source_proxy"
                    if is_trial_specific_proxy_fact(fact_code)
                    else "reusable_normalized_fact"
                ),
                "trial_count": len(trial_ids),
                "criterion_count": criterion_count_by_fact[fact_code],
                "trial_ids": trial_ids,
            }
        )

    shared_criterion_count = sum(
        row["criterion_count"] for row in rows if row["trial_count"] >= 2
    )
    return rows, shared_criterion_count


def _count_facts_at_least(rows: Sequence[Mapping[str, Any]], trial_count: int) -> int:
    return sum(int(row["trial_count"]) >= trial_count for row in rows)


def build_shared_fact_report(trial_set: Mapping[str, Any]) -> dict[str, Any]:
    """Build group-level and overall reuse counts from one structured trial set.

    A fact is shared when the same ``fact_code`` occurs in at least two distinct
    trials in the same disease group.  Repeated criteria inside one trial do not
    increase that fact's trial count.
    """

    groups = _required_sequence(trial_set, "groups")
    trials = _required_sequence(trial_set, "trials")
    criteria = _required_sequence(trial_set, "criteria")

    group_metadata: dict[str, Mapping[str, Any]] = {}
    for group in groups:
        group_id = _required_text(group, "group_id")
        if group_id in group_metadata:
            raise ValueError(f"duplicate group_id: {group_id}")
        group_metadata[group_id] = group

    trial_ids_by_group: dict[str, set[str]] = defaultdict(set)
    for trial in trials:
        group_id = _required_text(trial, "group_id")
        if group_id not in group_metadata:
            raise ValueError(f"trial references unknown group_id: {group_id}")
        trial_ids_by_group[group_id].add(_required_text(trial, "nct_id"))

    criteria_by_group: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for criterion in criteria:
        group_id = _required_text(criterion, "group_id")
        if group_id not in group_metadata:
            raise ValueError(f"criterion references unknown group_id: {group_id}")
        trial_id = _required_text(criterion, "nct_id")
        if trial_id not in trial_ids_by_group[group_id]:
            raise ValueError(
                f"criterion references trial {trial_id!r} outside group {group_id!r}"
            )
        criteria_by_group[group_id].append(criterion)

    group_reports: list[dict[str, Any]] = []
    all_group_fact_rows: list[dict[str, Any]] = []
    total_shared_criteria = 0

    for group_id, group in group_metadata.items():
        group_criteria = criteria_by_group[group_id]
        fact_rows, shared_criterion_count = _fact_rows(group_criteria)
        trial_count = len(trial_ids_by_group[group_id])
        reusable_rows = [
            row for row in fact_rows if row["fact_type"] == "reusable_normalized_fact"
        ]
        proxy_rows = [
            row for row in fact_rows if row["fact_type"] == "trial_specific_source_proxy"
        ]

        all_group_fact_rows.extend(
            {"group_id": group_id, **row} for row in fact_rows
        )
        total_shared_criteria += shared_criterion_count
        criterion_count = len(group_criteria)
        group_reports.append(
            {
                "group_id": group_id,
                "group_label": _required_text(group, "group_label"),
                "trial_count": trial_count,
                "criterion_count": criterion_count,
                "unique_group_specific_fact_count": len(fact_rows),
                "reusable_normalized_fact_count": len(reusable_rows),
                "trial_specific_proxy_fact_count": len(proxy_rows),
                "facts_used_by_at_least_2_trials": _count_facts_at_least(
                    fact_rows, 2
                ),
                "facts_used_by_at_least_3_trials": _count_facts_at_least(
                    fact_rows, 3
                ),
                "facts_used_by_all_trials": _count_facts_at_least(
                    fact_rows, trial_count
                ),
                "criteria_whose_fact_is_used_by_at_least_2_trials": (
                    shared_criterion_count
                ),
                "share_of_criteria_with_a_cross_trial_fact": (
                    shared_criterion_count / criterion_count if criterion_count else 0.0
                ),
                "facts": fact_rows,
            }
        )

    criterion_count = len(criteria)
    reusable_group_facts = [
        row
        for row in all_group_fact_rows
        if row["fact_type"] == "reusable_normalized_fact"
    ]
    proxy_group_facts = [
        row
        for row in all_group_fact_rows
        if row["fact_type"] == "trial_specific_source_proxy"
    ]
    shared_criterion_count_by_fact_code: dict[str, int] = defaultdict(int)
    for row in all_group_fact_rows:
        if int(row["trial_count"]) >= 2:
            shared_criterion_count_by_fact_code[str(row["fact_code"])] += int(
                row["criterion_count"]
            )

    return {
        "report_id": "public-protocol-shared-facts-v1",
        "scope": {
            "description": (
                "Descriptive structure of the selected public-protocol benchmark"
            ),
            "group_count": len(groups),
            "trial_count": len(trials),
            "criterion_count": criterion_count,
            "shared_fact_definition": (
                "The same fact_code appears in at least two distinct trials "
                "within one disease group"
            ),
            "group_specific_counting": (
                "The same normalized fact in two disease groups is counted once "
                "per group"
            ),
            "proxy_fact_pattern": PROXY_FACT_PATTERN.pattern,
            "interpretation_boundary": (
                "These counts describe this selected structured benchmark; they "
                "do not estimate clinical prevalence or screening accuracy."
            ),
        },
        "overall": {
            "unique_group_specific_fact_count": len(all_group_fact_rows),
            "reusable_normalized_group_fact_count": len(reusable_group_facts),
            "trial_specific_proxy_group_fact_count": len(proxy_group_facts),
            "facts_used_by_at_least_2_trials": _count_facts_at_least(
                all_group_fact_rows, 2
            ),
            "facts_used_by_at_least_3_trials": _count_facts_at_least(
                all_group_fact_rows, 3
            ),
            "facts_used_by_all_5_trials": sum(
                row["trial_count"] == 5 for row in all_group_fact_rows
            ),
            "criteria_whose_fact_is_used_by_at_least_2_trials": total_shared_criteria,
            "share_of_criteria_with_a_cross_trial_fact": (
                total_shared_criteria / criterion_count if criterion_count else 0.0
            ),
            "shared_criterion_count_by_fact_code": dict(
                sorted(shared_criterion_count_by_fact_code.items())
            ),
        },
        "groups": group_reports,
    }


def render_shared_fact_report_markdown(report: Mapping[str, Any]) -> str:
    """Render the report as a compact Korean table for human inspection."""

    scope = report["scope"]
    overall = report["overall"]
    lines = [
        "# 여러 임상시험에서 함께 쓰이는 환자 정보",
        "",
        (
            f"공개 임상시험 {scope['trial_count']}건에서 구조화한 조건 "
            f"{scope['criterion_count']}개를 대상으로 계산했다. 같은 질환 안에서 "
            "동일한 환자 정보가 서로 다른 시험 두 곳 이상에 쓰이면 공통 정보로 셌다. "
            "한 시험 안에서 같은 정보가 여러 조건에 반복돼도 시험 수는 한 번만 센다."
        ),
        "",
        "| 전체 집계 | 수 |",
        "|---|---:|",
        (
            "| 질환별로 구분한 환자 정보 | "
            f"{overall['unique_group_specific_fact_count']}개 |"
        ),
        (
            "| 여러 시험에서 다시 쓸 수 있게 이름을 통일한 정보 | "
            f"{overall['reusable_normalized_group_fact_count']}개 |"
        ),
        (
            "| 특정 시험의 원문 한 줄만 나타내는 임시 항목 | "
            f"{overall['trial_specific_proxy_group_fact_count']}개 |"
        ),
        (
            "| 같은 질환의 시험 2건 이상에 쓰인 정보 | "
            f"{overall['facts_used_by_at_least_2_trials']}개 |"
        ),
        (
            "| 같은 질환의 시험 3건 이상에 쓰인 정보 | "
            f"{overall['facts_used_by_at_least_3_trials']}개 |"
        ),
        (
            "| 같은 질환의 시험 5건 모두에 쓰인 정보 | "
            f"{overall['facts_used_by_all_5_trials']}개 |"
        ),
        (
            "| 여러 시험에 공통인 정보를 사용하는 조건 | "
            f"{overall['criteria_whose_fact_is_used_by_at_least_2_trials']} / "
            f"{scope['criterion_count']}개 "
            f"({overall['share_of_criteria_with_a_cross_trial_fact']:.1%}) |"
        ),
        (
            "| 그중 나이·임신/수유·활동성 감염 조건 | "
            f"{sum(overall['shared_criterion_count_by_fact_code'].get(code, 0) for code in ('age_years', 'pregnancy_or_lactation', 'active_serious_infection'))}개 |"
        ),
        "",
        "## 질환별 결과",
        "",
        (
            "| 질환 | 조건 | 환자 정보 | 이름을 통일한 정보 | 시험 전용 임시 항목 | "
            "2건 이상 | 3건 이상 | 5건 모두 | 공통 정보가 쓰인 조건 |"
        ),
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for group in report["groups"]:
        lines.append(
            "| {group_label} | {criterion_count} | {unique_group_specific_fact_count} | "
            "{reusable_normalized_fact_count} | {trial_specific_proxy_fact_count} | "
            "{facts_used_by_at_least_2_trials} | {facts_used_by_at_least_3_trials} | "
            "{facts_used_by_all_trials} | {shared} ({share:.1%}) |".format(
                **group,
                shared=group[
                    "criteria_whose_fact_is_used_by_at_least_2_trials"
                ],
                share=group["share_of_criteria_with_a_cross_trial_fact"],
            )
        )

    lines.extend(
        [
            "",
            "## 계산 범위",
            "",
            (
                "`age_years`, `active_serious_infection`처럼 여러 시험에서 같은 뜻으로 "
                "쓴 항목은 이름을 통일한 정보로 분류했다. "
                "`nct...source_line...`, `nct...logic_line...` 형식은 특정 시험의 "
                "원문 한 줄을 구조화하기 위해 만든 시험 전용 임시 항목이다."
            ),
            "",
            (
                "이 수치는 선택한 50개 시험과 구조화한 202개 조건의 구성만 설명한다. "
                "실제 진료에서 해당 정보가 얼마나 자주 필요한지, 시스템이 조건을 얼마나 "
                "정확히 판단하는지는 이 집계로 알 수 없다."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def write_shared_fact_report(
    *, trial_set_path: Path, output_dir: Path
) -> dict[str, Any]:
    """Read a trial set, calculate the report, and write JSON and Markdown."""

    trial_set = json.loads(trial_set_path.read_text(encoding="utf-8"))
    if not isinstance(trial_set, dict):
        raise ValueError("trial set root must be an object")
    report = build_shared_fact_report(trial_set)

    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "shared-fact-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (output_dir / "shared-fact-report.md").write_text(
        render_shared_fact_report_markdown(report),
        encoding="utf-8",
    )
    return report
