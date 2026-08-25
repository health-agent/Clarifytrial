"""Assess whether the connected workflow is ready for external-model evaluation."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from ..io import atomic_write_text


def _read(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _gate(
    gate_id: str,
    title: str,
    passed: bool,
    evidence: str,
) -> dict[str, Any]:
    return {
        "gate_id": gate_id,
        "title": title,
        "passed": passed,
        "evidence": evidence,
    }


def _group_topology_signatures(trial_set: dict[str, Any]) -> set[tuple[Any, ...]]:
    """Summarize how facts are shared across trials in each disease group."""

    criteria_by_group: dict[str, list[dict[str, Any]]] = {}
    for criterion in trial_set.get("criteria", []):
        criteria_by_group.setdefault(str(criterion["group_id"]), []).append(criterion)
    signatures = set()
    for rows in criteria_by_group.values():
        trial_counts = Counter(str(row["nct_id"]) for row in rows)
        fact_counts = Counter(str(row["fact_code"]) for row in rows)
        signatures.add(
            (
                tuple(sorted(trial_counts.values())),
                tuple(sorted(fact_counts.values())),
            )
        )
    return signatures


def build_final_evaluation_readiness(
    *,
    trial_set_path: str | Path,
    patient_pairs_path: str | Path,
    workflow_summary_path: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    trial_set = _read(trial_set_path)
    pairs = _read(patient_pairs_path)
    workflow = _read(workflow_summary_path)
    output = Path(output_dir)

    missing_counts = Counter(
        len(pair["pivotal_fact_codes"]) for pair in pairs["pairs"]
    )
    acquisition_modes = set(pairs.get("acquisition_mode_counts", {}))
    missing_count_text = "·".join(
        f"{missing_count}개 누락 {patient_count}명"
        for missing_count, patient_count in sorted(missing_counts.items())
    )
    topology_signatures = _group_topology_signatures(trial_set)
    declared_layouts = {
        str(group["layout_variant"])
        for group in pairs.get("groups", [])
        if group.get("layout_variant")
    }
    structure_variant_count = max(
        len(topology_signatures),
        len(declared_layouts),
    )
    current = next(
        row for row in workflow["arm_metrics"] if row["arm"] == "clarifytrial"
    )
    unavailable = next(
        (
            row
            for row in workflow.get("unavailable_answer_metrics", [])
            if row["arm"] == "clarifytrial"
        ),
        None,
    )
    declined = next(
        (
            row
            for row in workflow.get("patient_declines_new_tests_metrics", [])
            if row["arm"] == "clarifytrial"
        ),
        None,
    )
    dataset_breadth = (
        pairs.get("group_count", 0) >= 10
        and pairs.get("patient_count", 0) >= 50
        and pairs.get("trial_count", 0) >= 50
        and pairs.get("complete_confirmed_candidate_count", 0) > 0
        and pairs.get("complete_ineligible_count", 0) > 0
        and {1, 2, 3, 5}.issubset(missing_counts)
        and len(acquisition_modes) >= 4
        and structure_variant_count >= 3
    )
    connected_stability = (
        current["failed_patient_count"] == 0
        and current["false_candidate_removals"] == 0
        and current["premature_final_confirmations"] == 0
        and current["repeated_fact_action_count"] == 0
    )
    rescue_is_measurable = (
        current.get("rescue_opportunity_count", 0) > 0
        and current.get("confirmed_rescue_count", 0) > 0
        and current.get("false_preservation_count", 0) > 0
        and current.get("false_preservation_resolved_count", 0) > 0
    )
    burden_is_visible = (
        current.get("patient_choice_action_count", 0) > 0
        and current.get("new_test_count", 0) > 0
        and current.get("additional_visit_count", 0) > 0
        and declined is not None
        and declined.get("new_test_count", 0) == 0
        and declined.get("additional_visit_count", 0) == 0
    )
    unavailable_fallback = (
        unavailable is not None
        and unavailable["unavailable_action_count"] > 0
        and unavailable["repeated_fact_action_count"] == 0
    )
    source_snapshot = trial_set.get("source_snapshot", {})
    source_coverage = trial_set.get("source_coverage", {})
    public_criteria = (
        trial_set.get("status") == "public_protocol_derived_benchmark"
        and trial_set.get("criterion_count", 0) >= 100
        and source_snapshot.get("sha256")
        and source_snapshot.get("commit")
        and source_coverage.get("structured_eligibility_source_line_count", 0) > 0
        and trial_set.get("explicit_non_all_logic_trial_count", 0) > 0
    )
    broad_metrics = workflow.get("broad_search_metrics") or {}
    broad_search = (
        bool(
            workflow.get("evaluation_scope", {}).get(
                "includes_broad_corpus_search"
            )
        )
        and broad_metrics.get("target_trial_count", 0) > 0
        and broad_metrics.get("target_recall", 0) > 0
    )
    external_model = workflow.get("model") != "deterministic-workflow"
    gold_standard = pairs.get("gold_standard", {})
    independent_gold = bool(
        gold_standard.get("independent_from_runtime_evaluator") is True
        and gold_standard.get("frozen_before_final_run") is True
    )

    complex_logic_group_count = trial_set.get(
        "explicit_non_all_logic_group_count",
        trial_set.get("explicit_non_all_logic_trial_count"),
    )
    gates = [
        _gate(
            "G1",
            "질환과 정보 부족 상태가 충분히 넓은가",
            dataset_breadth,
            (
                f"질환 {pairs.get('group_count')}개, 합성 환자 "
                f"{pairs.get('patient_count')}명, 시험 {pairs.get('trial_count')}개, "
                f"{missing_count_text}, "
                f"시험-정보 연결 구조 {structure_variant_count}종"
            ),
        ),
        _gate(
            "G2",
            "전체 흐름이 잘못 제외하거나 성급하게 확정하지 않는가",
            connected_stability,
            (
                f"실행 오류 {current['failed_patient_count']}명, 잘못 제외 "
                f"{current['false_candidate_removals']}개, 성급한 확정 "
                f"{current['premature_final_confirmations']}개"
            ),
        ),
        _gate(
            "G3",
            "후보 보존과 실제 회복을 따로 측정할 수 있는가",
            rescue_is_measurable,
            (
                f"되살릴 수 있었던 후보 {current.get('rescue_opportunity_count')}개, "
                f"실제 확정 {current.get('confirmed_rescue_count')}개, 결국 제외될 "
                f"후보 정리 {current.get('false_preservation_resolved_count')}개"
            ),
        ),
        _gate(
            "G4",
            "새 검사와 추가 방문의 대가가 결과에 남는가",
            burden_is_visible,
            (
                "새 검사를 허용한 실행에서 환자 선택이 필요한 확인 "
                f"{current.get('patient_choice_action_count')}회, 새 검사 "
                f"{current.get('new_test_count')}회; 새 검사를 거절한 실행에서 "
                f"새 검사 {None if declined is None else declined.get('new_test_count')}회"
            ),
        ),
        _gate(
            "G5",
            "답을 얻지 못해도 같은 정보를 반복하지 않는가",
            unavailable_fallback,
            (
                "평가 없음"
                if unavailable is None
                else (
                    f"답을 얻지 못한 확인 {unavailable['unavailable_action_count']}회, "
                    f"같은 정보 반복 {unavailable['repeated_fact_action_count']}회"
                )
            ),
        ),
        _gate(
            "G6",
            "실제 공개 시험 조건에 연결된 자료인가",
            public_criteria,
            (
                (
                    f"공개 시험 {trial_set.get('trial_count')}건에서 원문 근거가 있는 "
                    f"조건 {trial_set.get('criterion_count')}개와 복합 조건 "
                    f"{complex_logic_group_count}묶음 사용"
                )
                if public_criteria
                else "현재 자료는 기능을 흔들어 보기 위한 합성 시험 조건"
            ),
        ),
        _gate(
            "G7",
            "589건 검색과 뒤쪽 흐름이 같은 환자 사례로 연결되는가",
            broad_search,
            (
                (
                    f"모집 중 시험 {broad_metrics.get('corpus_trial_count')}건에서 "
                    f"서로 다른 평가 시험 "
                    f"{broad_metrics.get('unique_target_trial_count')}건을 환자별로 "
                    f"반복한 {broad_metrics.get('target_patient_trial_count')}개 연결 중 "
                    f"{broad_metrics.get('retrieved_target_patient_trial_count')}개 검색. "
                    "검색된 다른 시험은 이 검사에서 조건 판정하지 않음"
                )
                if broad_search
                else "질환별 시험 5개를 미리 정한 상태에서 시작"
            ),
        ),
        _gate(
            "G8",
            "외부 모델로 전체 역할 호출을 실행했는가",
            external_model,
            (
                f"사용 모델: {workflow.get('model')}"
            ),
        ),
        _gate(
            "G9",
            "실행 규칙과 별도로 만든 정답을 사용하는가",
            independent_gold,
            (
                str(gold_standard.get("description"))
                if independent_gold
                else "현재 기대 결과는 같은 판정 규칙으로 만들었으므로 프로그램 연결 검사에만 사용"
            ),
        ),
    ]
    software_ready = all(item["passed"] for item in gates[:7])
    external_completed = software_ready and external_model
    independent_performance_ready = all(item["passed"] for item in gates)
    payload = {
        "protocol_id": "clarifytrial-external-evaluation-readiness-v2",
        "deterministic_integration_check_completed": software_ready,
        "software_ready_for_external_model_evaluation": software_ready,
        "external_model_evaluation_completed": external_completed,
        "independent_performance_claim_ready": independent_performance_ready,
        "gates": gates,
        "conclusion": (
            "결정론적 연결 검사는 끝났다. 독립 정답 자료와 외부 모델 실행이 남았다."
            if software_ready and not independent_performance_ready
            else (
                "독립 정답 자료에서 외부 모델 평가까지 끝났다."
                if independent_performance_ready
                else "외부 모델 평가 전에 해결할 연결 항목이 남아 있다."
            )
        ),
    }
    output.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        output / "readiness.json",
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    lines = [
        "# ClarifyTrial 외부 모델 평가 준비 상태",
        "",
        (
            "공개 시험 검색, 조건 판정, 질문 선택, 답변 반영, 환자 부담 기록을 "
            "하나의 사례로 연결하는 검사는 끝났다. 이 수치는 프로그램이 정해진 "
            "규칙대로 이어지는지를 확인하며 모델 성능을 뜻하지 않는다."
            if software_ready
            else payload["conclusion"]
        ),
        "",
        "| 확인 항목 | 결과 | 근거 |",
        "|---|---|---|",
    ]
    for gate in gates:
        lines.append(
            f"| {gate['title']} | {'통과' if gate['passed'] else '남음'} | "
            f"{gate['evidence']} |"
        )
    lines.extend(
        [
            "",
            "## 다음 순서",
            "",
            "1. 현재 판정 코드와 별도로 정답을 만든 새 시험 자료를 고정한다.",
            "2. 외부 모델로 같은 전체 흐름을 실행한다.",
            "3. 질문 전후 판단 변화, 새 검사·방문 수, 오류 종류, 호출 수와 토큰을 함께 기록한다.",
            "",
        ]
    )
    atomic_write_text(output / "readiness.md", "\n".join(lines))
    return payload


__all__ = ["build_final_evaluation_readiness"]
