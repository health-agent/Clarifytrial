"""Assess whether the connected workflow is ready for its final evaluation."""

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
    layout_variants = {
        group.get("layout_variant")
        for group in pairs.get("groups", [])
        if group.get("layout_variant")
    }
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
        and len(layout_variants) >= 3
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
    public_criteria = trial_set.get("status") != "synthetic_maturity_benchmark"
    broad_search = bool(
        workflow.get("evaluation_scope", {}).get("includes_broad_corpus_search")
    )
    external_model = workflow.get("model") != "deterministic-workflow"

    gates = [
        _gate(
            "G1",
            "질환과 정보 부족 상태가 충분히 넓은가",
            dataset_breadth,
            (
                f"질환 {pairs.get('group_count')}개, 합성 환자 "
                f"{pairs.get('patient_count')}명, 시험 {pairs.get('trial_count')}개, "
                f"{missing_count_text}, "
                f"시험-정보 연결 구조 {len(layout_variants)}종"
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
                "공개 시험 조건 사용"
                if public_criteria
                else "현재 자료는 기능을 흔들어 보기 위한 합성 시험 조건"
            ),
        ),
        _gate(
            "G7",
            "넓은 시험 검색부터 같은 사례로 평가했는가",
            broad_search,
            (
                "넓은 검색 포함"
                if broad_search
                else "질환별 시험 5개를 미리 정한 상태에서 시작"
            ),
        ),
        _gate(
            "G8",
            "최종 사용할 외부 모델로 실행했는가",
            external_model,
            (
                f"사용 모델: {workflow.get('model')}"
            ),
        ),
    ]
    software_ready = all(item["passed"] for item in gates[:5])
    final_ready = all(item["passed"] for item in gates)
    payload = {
        "protocol_id": "clarifytrial-final-evaluation-readiness-v1",
        "software_ready_for_source_anchored_evaluation": software_ready,
        "final_performance_claim_ready": final_ready,
        "gates": gates,
        "conclusion": (
            "The connected software is mature enough to receive the final "
            "source-anchored dataset, but final performance claims remain blocked."
            if software_ready and not final_ready
            else (
                "All declared final-evaluation gates passed."
                if final_ready
                else "The connected software still has blocking maturity failures."
            )
        ),
    }
    output.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        output / "readiness.json",
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    lines = [
        "# ClarifyTrial 최종 성능평가 준비 상태",
        "",
        (
            "현재 프로그램은 실제 공개 시험 조건으로 최종 평가를 시작할 수 있는 "
            "기능 수준에 도달했다. 다만 지금 실행은 합성 시험 조건과 미리 정한 "
            "후보 5개를 사용했으므로 최종 성능 수치로 발표할 단계는 아니다."
            if software_ready and not final_ready
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
            "1. 이미 고른 공개 시험 50건에서 계산 가능한 참가 조건을 정리한다.",
            "2. 새 합성 환자를 그 실제 조건과 연결한다.",
            "3. 모집 가능한 589건 검색부터 질문 뒤 순위 변경까지 한 사례로 실행한다.",
            "4. 마지막에 사용할 모델로 호출 수·토큰·회복 결과를 다시 계산한다.",
            "",
        ]
    )
    atomic_write_text(output / "readiness.md", "\n".join(lines))
    return payload


__all__ = ["build_final_evaluation_readiness"]
