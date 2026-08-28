"""Small terminal view for the standardized synthetic question workflow."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .datasets.natural_policy_evaluation import run_natural_policy_evaluation
from .disclaimer import read_medical_disclaimer


_CANDIDATE_LABELS = {
    "retain": "후보 유지",
    "remove": "후보 제외",
    "uncertain": "후보 판단 보류",
}
_CONFIRMATION_LABELS = {
    "confirmed": "조건 확인 완료",
    "not_confirmed": "추가 확인 필요",
    "ineligible": "참가 조건 불충족",
    "uncertain": "판단 보류",
}
_ACTION_LABELS = {
    "ASK_PATIENT": "환자에게 직접 확인",
    "LOOKUP_RECORD": "기존 기록 확인",
    "REQUEST_VERIFICATION": "공식 결과 또는 의료진 확인",
}
_GROUP_LABELS = {
    "breast_cancer": "유방암",
    "major_depressive_disorder": "주요우울장애",
    "type_2_diabetes": "제2형 당뇨병",
}
_FACT_LABELS = {
    "ability_to_refrain_from_nicotine_during_dosing_session": (
        "검사 시간 동안 니코틴 사용을 멈출 수 있는지"
    ),
    "absolute_neutrophil_count": "절대호중구 수치",
    "age": "환자 나이",
    "albumin_adjusted_serum_calcium": "알부민 보정 혈중 칼슘 수치",
    "body_mass_index": "체질량지수(BMI)",
    "dsm5_major_depressive_disorder": "주요우울장애 진단 여부",
    "english_speaking": "영어로 의사소통할 수 있는지",
    "hba1c_at_screening": "선별검사 때의 당화혈색소 수치",
    "isi_score": "불면증 심각도 점수",
    "platelet_count": "혈소판 수치",
    "prior_systemic_treatment_count": "이전에 받은 전신치료 횟수",
    "sleep_disorder_diagnosis": "수면장애 진단 여부",
}


def _decision_label(item: Mapping[str, Any]) -> str:
    return (
        f"{_CANDIDATE_LABELS[str(item['candidate_status'])]} / "
        f"{_CONFIRMATION_LABELS[str(item['confirmation_status'])]}"
    )


def _answer_value(item: Mapping[str, Any]) -> str:
    value = float(item["value"])
    unit = str(item["unit"])
    if unit in {"bool", "boolean"}:
        return "예" if value == 1 else "아니오"
    unit_label = {
        "years": "세",
        "years old": "세",
        "treatments": "회",
        "ISI score": "점",
        "kg/m^2": "kg/m²",
    }.get(unit, unit)
    return f"{value:g}{unit_label}"


def _print_decisions(
    decisions: Sequence[Mapping[str, Any]],
    write: Callable[[str], None],
) -> None:
    for item in decisions:
        write(f"- {item['trial_id']}: {_decision_label(item)}")


def render_natural_question_run(
    *,
    run: Mapping[str, Any],
    patient: Mapping[str, Any],
    auto_advance: bool,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> None:
    """Show each selected question, synthetic answer, and decision change."""

    if write is print and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    input_label = (
        "핵심 값 다섯 개가 입력에서 모두 빠진 상태"
        if run["input_state"] == "fully_missing"
        else "핵심 값은 보이지만 확인 출처가 부족한 상태"
    )
    write("=" * 68)
    write("ClarifyTrial 질문 과정")
    write("=" * 68)
    write(f"합성 환자: {run['patient_id']}")
    write(f"질환 범위: {_GROUP_LABELS.get(run['group_id'], run['group_id'])}")
    write(f"검토할 임상시험: {run['final_metrics']['trial_count']}개")
    write(f"처음 입력: {input_label}")
    write(f"확인 가능한 질문 수: 최대 {run['action_budget']}개")
    write("")
    write("처음 판정")
    _print_decisions(run["trajectory"][0]["decisions"], write)

    for step in run["trajectory"][1:]:
        write("")
        write("-" * 68)
        write(f"{step['step']}번째 확인")
        fact_label = _FACT_LABELS.get(
            step["selected_fact_code"], step["fact_description"]
        )
        write(f"확인할 정보: {fact_label}")
        write(f"선택 이유: {step['selection_reason']}")
        write(f"확인 방법: {_ACTION_LABELS[step['selected_action']]}")
        write(
            "영향을 받는 임상시험: "
            + ", ".join(step["related_trial_ids"])
        )
        if not auto_advance:
            read("합성 답변을 확인하려면 Enter를 누르세요. ")
        answer_values = list(
            dict.fromkeys(
                _answer_value(item)
                for item in step["synthetic_answer_evidence"]
            )
        )
        write(f"검증용 합성 답변: {fact_label} {', '.join(answer_values)}")
        if step["decision_changes"]:
            write("답변 뒤 바뀐 판정")
            for change in step["decision_changes"]:
                before = _decision_label(
                    {
                        "candidate_status": change["before_candidate_status"],
                        "confirmation_status": change[
                            "before_confirmation_status"
                        ],
                    }
                )
                after = _decision_label(
                    {
                        "candidate_status": change["after_candidate_status"],
                        "confirmation_status": change[
                            "after_confirmation_status"
                        ],
                    }
                )
                write(f"- {change['trial_id']}: {before} → {after}")
        else:
            write("답변 뒤 최종 판정이 바뀐 시험은 없습니다.")

    write("")
    write("=" * 68)
    write("최종 판정")
    write("=" * 68)
    _print_decisions(run["final_decisions"], write)
    metrics = run["question_selection_metrics"]
    write("")
    write("질문 선택 점검")
    write(f"- 실제로 확인한 정보: {run['action_count']}개")
    write(
        "- 같은 질문 수에서 필요했던 정보를 고른 비율: "
        f"{metrics['needed_fact_recall'] * 100:.1f}%"
    )
    write(
        "- 결과 개선에 필요하지 않았던 확인: "
        f"{metrics['unnecessary_action_count']}개"
    )
    write(
        "- 미정에서 최종 판정으로 바뀐 임상시험: "
        f"{run['unresolved_to_resolved']}개"
    )
    write("")
    write(str(patient["medical_disclaimer"]))


def run_natural_text_demo(
    *,
    trial_set_path: str | Path,
    generation_config_path: str | Path,
    patient_pairs_path: str | Path,
    records_path: str | Path,
    destination: str | Path,
    patient_id: str,
    action_budget: int = 3,
    input_state: str = "fully-missing",
    auto_advance: bool = False,
    read: Callable[[str], str] = input,
    write: Callable[[str], None] = print,
) -> dict[str, Any]:
    """Run and display one deterministic synthetic question-policy example."""

    if input_state not in {"partly-known", "fully-missing"}:
        raise ValueError("input_state must be partly-known or fully-missing")
    patient_pairs_path = Path(patient_pairs_path)
    pairs_document = json.loads(patient_pairs_path.read_text(encoding="utf-8"))
    patient = next(
        (
            item
            for item in pairs_document["pairs"]
            if str(item["patient_id"]) == patient_id
        ),
        None,
    )
    if patient is None:
        raise ValueError(f"unknown patient ID: {patient_id}")

    run_natural_policy_evaluation(
        trial_set_path=trial_set_path,
        generation_config_path=generation_config_path,
        patient_pairs_path=patient_pairs_path,
        records_path=records_path,
        structure_result_paths=[],
        destination=destination,
        action_budget=action_budget,
        patient_ids=[patient_id],
        include_fully_missing=input_state == "fully-missing",
    )
    document = json.loads(Path(destination).read_text(encoding="utf-8"))
    stored_state = (
        "fully_missing" if input_state == "fully-missing" else "gold_structured"
    )
    run = next(
        item
        for item in document["runs"]
        if item["patient_id"] == patient_id
        and item["input_state"] == stored_state
        and item["policy_id"] == "clarifytrial_exact_coverage_v3"
    )
    render_natural_question_run(
        run=run,
        patient={**patient, "medical_disclaimer": read_medical_disclaimer()},
        auto_advance=auto_advance,
        read=read,
        write=write,
    )
    return {
        "output": str(destination),
        "patient_id": patient_id,
        "input_state": stored_state,
        "action_count": run["action_count"],
        "unresolved_to_resolved": run["unresolved_to_resolved"],
        "question_selection_metrics": run["question_selection_metrics"],
    }


__all__ = ["render_natural_question_run", "run_natural_text_demo"]
