"""Summarize one completed multi-topic challenge run from saved artifacts."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_trace(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _find_event(
    trace: list[dict[str, Any]], event_name: str
) -> dict[str, Any] | None:
    return next((item for item in trace if item.get("event") == event_name), None)


def _source_span_failures(
    topic_id: str,
    prepared_trials: list[dict[str, Any]],
) -> list[list[str]]:
    failures: list[list[str]] = []
    for row in prepared_trials:
        trial_id = row["trial_id"]
        source_text = row.get("eligibility_text")
        if not isinstance(source_text, str) or not source_text:
            failures.append([topic_id, trial_id, "*", "missing_source_text"])
            continue
        for criterion in row["trial"]["criteria"]:
            criterion_id = criterion["criterion_id"]
            location = criterion.get("source_location", "")
            if "#chars=" not in location:
                failures.append(
                    [topic_id, trial_id, criterion_id, "missing_character_range"]
                )
                continue
            try:
                start_text, end_text = location.rsplit("#chars=", 1)[1].split("-", 1)
                start, end = int(start_text), int(end_text)
            except (TypeError, ValueError):
                failures.append(
                    [topic_id, trial_id, criterion_id, "invalid_character_range"]
                )
                continue
            if source_text[start:end] != criterion["statement"]:
                failures.append([topic_id, trial_id, criterion_id, "text_mismatch"])
    return failures


def _candidate_trials(prepared_trials: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "trial_id": row["trial_id"],
            "title": row["title"],
            "declared_conditions": row.get("conditions", []),
            "source_location": row["source_location"],
        }
        for row in prepared_trials
    ]


def audit_run(run_dir: Path) -> dict[str, Any]:
    topic_summaries: list[dict[str, Any]] = []
    source_failures: list[list[str]] = []
    role_call_count: Counter[str] = Counter()
    role_token_count: Counter[str] = Counter()
    models: set[str] = set()
    input_paths: set[str] = set()

    for topic_dir in sorted(
        path for path in run_dir.iterdir() if path.is_dir() and path.name.startswith("S")
    ):
        result = _read_json(topic_dir / "result.json")
        trace = _read_trace(topic_dir / "trace.jsonl")
        prepared_path = topic_dir / "prepared-trials.json"
        prepared_trials = _read_json(prepared_path) if prepared_path.is_file() else []
        screening = result.get("screening") or {}
        input_data = result.get("input") or {}
        usage = result.get("usage") or {}
        models.add(str(result.get("model", "unknown")))
        input_path = input_data.get("challenge_topics_path")
        if input_path:
            input_paths.add(str(input_path))

        retrieved_event = _find_event(trace, "candidate_trials_retrieved")
        filtered_event = _find_event(trace, "candidate_trials_filtered")
        patient_event = _find_event(trace, "patient_record_structured")
        retrieved = (
            [] if retrieved_event is None else retrieved_event["output"].get("trial_ids", [])
        )
        removed = (
            [] if filtered_event is None else filtered_event["output"].get("removed", [])
        )
        search_conditions = (
            input_data.get("search_conditions", [])
            if patient_event is None
            else patient_event["output"].get("search_conditions", [])
        )
        decisions = screening.get("final_decisions", [])
        status_counts = Counter(
            item.get("confirmation_status", "unknown") for item in decisions
        )
        criteria = [
            criterion
            for row in prepared_trials
            for criterion in row["trial"]["criteria"]
        ]
        blocked = sum(
            not row["trial"].get("protocol_logic_supported", True)
            for row in prepared_trials
        )
        topic_failures = _source_span_failures(topic_dir.name, prepared_trials)
        source_failures.extend(topic_failures)

        for role, role_usage in usage.get("by_role", {}).items():
            role_call_count[role] += int(role_usage.get("call_count", 0))
            role_token_count[role] += int(role_usage.get("total_tokens", 0))

        topic_summaries.append(
            {
                "topic": topic_dir.name,
                "search_conditions": search_conditions,
                "retrieved_count": len(retrieved),
                "removed_by_relevance_review": len(removed),
                "relevant_not_selected_count": (
                    len(retrieved) - len(removed) - len(prepared_trials)
                ),
                "kept_candidate_count": len(prepared_trials),
                "criterion_count": len(criteria),
                "inclusion_count": sum(
                    item.get("kind") == "inclusion" for item in criteria
                ),
                "exclusion_count": sum(
                    item.get("kind") == "exclusion" for item in criteria
                ),
                "automatic_confirmation_blocked_trial_count": blocked,
                "confirmed_count": status_counts["confirmed"],
                "pending_count": (
                    status_counts["not_confirmed"] + status_counts["uncertain"]
                ),
                "excluded_count": status_counts["ineligible"],
                "information_attempt_count": len(screening.get("action_history", [])),
                "selective_review_count": len(screening.get("review_history", [])),
                "model_call_count": int(usage.get("call_count", 0)),
                "total_tokens": int(usage.get("total_tokens", 0)),
                "status": (
                    "screened" if screening else result.get("status", "unknown")
                ),
                "candidate_trials": _candidate_trials(prepared_trials),
                "source_span_failure_count": len(topic_failures),
            }
        )

    total_fields = (
        "retrieved_count",
        "removed_by_relevance_review",
        "relevant_not_selected_count",
        "kept_candidate_count",
        "criterion_count",
        "inclusion_count",
        "exclusion_count",
        "automatic_confirmation_blocked_trial_count",
        "confirmed_count",
        "pending_count",
        "excluded_count",
        "information_attempt_count",
        "selective_review_count",
        "model_call_count",
        "total_tokens",
    )
    totals = {key: sum(item[key] for item in topic_summaries) for key in total_fields}
    totals["topic_count"] = len(topic_summaries)
    official_source_trials = sum(
        trial["source_location"].startswith("https://clinicaltrials.gov/study/")
        for topic in topic_summaries
        for trial in topic["candidate_trials"]
    )
    return {
        "run": {
            "input": sorted(input_paths),
            "topic_count": len(topic_summaries),
            "candidate_limit_per_topic": 3,
            "model": sorted(models),
            "answer_policy": "All requested information was answered as unknown.",
        },
        "totals": totals,
        "role_call_count": dict(sorted(role_call_count.items())),
        "role_token_count": dict(sorted(role_token_count.items())),
        "official_source_trial_count": official_source_trials,
        "source_span_failure_count": len(source_failures),
        "source_span_failures": source_failures,
        "topics": topic_summaries,
    }


def render_markdown(summary: dict[str, Any]) -> str:
    totals = summary["totals"]
    lines = [
        "# 공식 환자 10명 전체 실행 점검",
        "",
        "- 입력: 대회 공식 합성 환자 10명",
        "- 실행: ClinicalTrials.gov 검색 → 질환 확인 → 조건 원문 정리 → 조건 판단 → 필요한 정보 선택 → 결과 저장",
        "- 답변: 전체 흐름 연결을 보기 위해 모든 질문에 `unknown` 입력",
        f"- 검색 원자료 후보: {totals['retrieved_count']}건",
        f"- 질환이 맞지 않아 제거한 시험: {totals['removed_by_relevance_review']}건",
        f"- 질환은 관련 있지만 상위 3개 밖이라 판정하지 않은 시험: {totals['relevant_not_selected_count']}건",
        f"- 실제 조건 판정으로 넘긴 시험: {totals['kept_candidate_count']}건",
        f"- 구조화한 선정·제외 조건: {totals['criterion_count']}개",
        f"- 원문과 글자 위치가 맞지 않은 조건: {summary['source_span_failure_count']}개",
        f"- 모델 호출: {totals['model_call_count']}회, {totals['total_tokens']:,}토큰",
        "",
        "| 환자 | 검색 후보 → 실제 판정 | 조건 | 후보 유지 | 제외 | 확인 시도 | 모델 호출 | 토큰 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in summary["topics"]:
        lines.append(
            "| {topic} | {retrieved_count} → {kept_candidate_count} | "
            "{criterion_count} | {pending_count} | {excluded_count} | "
            "{information_attempt_count} | {model_call_count} | {total_tokens:,} |".format(
                **item
            )
        )
    lines.extend(["", "## 실제 판정으로 넘긴 시험", ""])
    for item in summary["topics"]:
        lines.append(f"### {item['topic']}")
        if not item["candidate_trials"]:
            lines.append("- 관련 모집 시험 없음")
        for trial in item["candidate_trials"]:
            conditions = ", ".join(trial["declared_conditions"]) or "등록 질환 없음"
            lines.append(
                f"- {trial['trial_id']} · {trial['title']} · 등록 질환: {conditions}"
            )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    args = parser.parse_args()
    summary = audit_run(args.run_dir)
    (args.run_dir / "audit-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (args.run_dir / "audit-summary.md").write_text(
        render_markdown(summary),
        encoding="utf-8",
    )
    print(
        f"topics={summary['totals']['topic_count']} "
        f"trials={summary['totals']['kept_candidate_count']} "
        f"criteria={summary['totals']['criterion_count']} "
        f"source_failures={summary['source_span_failure_count']}"
    )


if __name__ == "__main__":
    main()
