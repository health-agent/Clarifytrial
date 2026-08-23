from __future__ import annotations

import json
from pathlib import Path

from clarifytrial.reporting import build_research_report


def _question_document(current: float) -> dict:
    common = {
        "action_budget": 3,
        "split": "heldout",
        "input_state": "fully_missing",
        "patient_count": 30,
        "mean_action_count": 3.0,
        "mean_needed_fact_recall": 0.6,
        "mean_unnecessary_action_count": 1.0,
    }
    return {
        "summaries": [
            {**common, "policy_id": "fixed_source_order", "trial_status_recovery": 0.75},
            {
                **common,
                "policy_id": "clarifytrial_exact_coverage_v3",
                "trial_status_recovery": current,
                "mean_needed_fact_recall": 1.0,
                "mean_unnecessary_action_count": 0.3,
            },
        ]
    }


def test_report_figures_read_values_from_evaluation_json(tmp_path: Path) -> None:
    question_path = tmp_path / "question.json"
    question_path.write_text(json.dumps(_question_document(0.89)), encoding="utf-8")
    output = tmp_path / "report"
    build_research_report(destination=output, question_policy_path=question_path)
    first_svg = (output / "question-policy.svg").read_text(encoding="utf-8")
    assert "89.0%" in first_svg
    assert "75.0%" in first_svg
    assert "처음 빠진 정보 목록의 앞 3개" in first_svg
    assert "가장 많은 시험 판단을 끝낼" in first_svg
    report = (output / "report.md").read_text(encoding="utf-8")
    assert "모든 환자 정보를 알 때와 같은 판단에 도달한 시험 비율" in report
    assert "고정 순서" not in report

    question_path.write_text(json.dumps(_question_document(0.91)), encoding="utf-8")
    build_research_report(destination=output, question_policy_path=question_path)
    second_svg = (output / "question-policy.svg").read_text(encoding="utf-8")
    assert "91.0%" in second_svg
    assert "89.0%" not in second_svg
    assert (output / "metrics.csv").exists()
    assert (output / "report.md").exists()


def test_report_replaces_internal_labels_with_self_explanatory_korean(
    tmp_path: Path,
) -> None:
    burden_path = tmp_path / "burden.json"
    burden_path.write_text(
        json.dumps(
            {
                "adoption_comparison": {
                    "heldout": {
                        "baseline_recovery": 0.84,
                        "candidate_recovery": 0.79,
                        "constrained_baseline_feasible_recovery": 0.81,
                        "constrained_candidate_feasible_recovery": 0.885,
                        "constrained_new_test_visit_baseline": 65,
                        "constrained_new_test_visit_candidate": 0,
                        "urgent_mean_delay_baseline": 62.03,
                        "urgent_mean_delay_candidate": 58.31,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    workflow_path = tmp_path / "workflow.json"
    workflow_path.write_text(
        json.dumps(
            {
                "patient_count": 1,
                "action_budget": 3,
                "arm_metrics": [
                    {
                        "arm": "no_questions",
                        "patient_count": 1,
                        "trial_status_recovery": 0.4,
                        "candidate_status_accuracy": 0.8,
                        "confirmation_status_accuracy": 0.5,
                        "false_candidate_removals": 1,
                        "premature_initial_confirmations": 0,
                        "mean_unresolved_to_resolved": 0.0,
                        "mean_action_count": 0,
                        "model_call_count": 1,
                        "total_tokens": 0,
                        "failed_patient_count": 0,
                    },
                    {
                        "arm": "fixed_order",
                        "patient_count": 1,
                        "trial_status_recovery": 0.6,
                        "candidate_status_accuracy": 0.9,
                        "confirmation_status_accuracy": 0.7,
                        "false_candidate_removals": 0,
                        "premature_initial_confirmations": 0,
                        "mean_unresolved_to_resolved": 1.0,
                        "mean_action_count": 3,
                        "model_call_count": 7,
                        "total_tokens": 0,
                        "failed_patient_count": 0,
                    },
                    {
                        "arm": "clarifytrial",
                        "patient_count": 1,
                        "trial_status_recovery": 0.8,
                        "candidate_status_accuracy": 0.95,
                        "confirmation_status_accuracy": 0.85,
                        "false_candidate_removals": 0,
                        "premature_initial_confirmations": 0,
                        "mean_unresolved_to_resolved": 1.5,
                        "mean_action_count": 3,
                        "model_call_count": 7,
                        "total_tokens": 0,
                        "failed_patient_count": 0,
                    },
                ],
                "paired_clarifytrial_vs_fixed": {
                    "patient_count": 1,
                    "mean_recovery_difference": 0.2,
                    "clarifytrial_better_patient_count": 1,
                    "equal_patient_count": 0,
                    "clarifytrial_worse_patient_count": 0,
                    "two_sided_exact_sign_test_p": 1.0,
                },
            }
        ),
        encoding="utf-8",
    )
    retrieval_path = tmp_path / "retrieval.json"
    retrieval_path.write_text(
        json.dumps(
            {
                "config": {"corpus_name": "trec_2021"},
                "corpus_documents": 26149,
                "metric_rows": [{"depth": 500, "weighted_recall": 0.8359}],
            }
        ),
        encoding="utf-8",
    )

    output = tmp_path / "report"
    build_research_report(
        destination=output,
        burden_path=burden_path,
        workflow_path=workflow_path,
        retrieval_paths=[retrieval_path],
    )
    report = (output / "report.md").read_text(encoding="utf-8")

    assert "환자가 새 검사나 추가 방문을 피해야 한다고 입력했는데도" in report
    assert "추가 정보를 확인하지 않고 처음 환자 자료만 사용" in report
    assert "검색 결과 상위 500개 안에 남긴 비율" in report
    assert "no_questions" not in report
    assert "fixed_order" not in report
    assert "trec_2021" not in report
    assert "| 고정 방식 |" not in report
    assert "고정 응답기" not in report
    assert "조건 판단·질문 작성 단계를 실행한 총횟수" in report
    assert "후보 유지·제외를 맞힌 비율" in report
    assert "처음 자료가 부족한데 확정한 수" in report
    assert "더 좋았던 환자는 1명" in report
    assert "합성 환자를 만들 때 저장한 답만 반환하는 실험용 코드" in report
