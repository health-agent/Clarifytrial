from __future__ import annotations

import csv
import json
import re
from pathlib import Path

import pytest

from clarifytrial.interactive.shared_fact_report import write_shared_fact_report
from scripts.build_policy_scale_tables import (
    _burden_paired_rows,
    _integrated_model_smoke_row,
    _model_role_routing_change_row,
    _public_protocol_efficiency_rows,
    _shared_fact_coverage_row,
    _statistical_unit_audit_rows,
)
from scripts.render_presentation_evidence_figures import (
    REQUIRED_INPUTS,
    _shared_coverage,
    _validate_inputs,
)


ROOT = Path(__file__).resolve().parents[1]
TRIAL_SET = ROOT / "data/public_protocol_benchmark_v1/trial_set.json"
PRESENTATION_RESULTS = ROOT / "docs/internal/results/presentation-evidence-v2"
PRESENTATION_PACKET = ROOT / "docs/internal/CLARIFYTRIAL_REPORT_PRESENTATION_PACKET.md"


def test_public_shared_fact_report_is_packaged_for_presentation(
    tmp_path: Path,
) -> None:
    report_dir = tmp_path / "public-protocol-shared-facts-v1"
    write_shared_fact_report(
        trial_set_path=TRIAL_SET,
        output_dir=report_dir,
    )

    [row] = _shared_fact_coverage_row(report_dir / "shared-fact-report.json")

    assert {
        "criterion_count": row["criterion_count"],
        "shared_criterion_count": row[
            "criteria_whose_fact_is_used_by_at_least_2_trials"
        ],
        "age": row["age_years_shared_criterion_count"],
        "pregnancy": row[
            "pregnancy_or_lactation_shared_criterion_count"
        ],
        "infection": row[
            "active_serious_infection_shared_criterion_count"
        ],
        "other": row["other_shared_criterion_count"],
    } == {
        "criterion_count": 202,
        "shared_criterion_count": 130,
        "age": 72,
        "pregnancy": 36,
        "infection": 18,
        "other": 4,
    }
    assert row["share_of_criteria_with_a_cross_trial_fact"] == pytest.approx(
        130 / 202
    )

    rendered_values = _shared_coverage(
        [{key: str(value) for key, value in row.items()}]
    )
    assert rendered_values == pytest.approx(
        (100 * 130 / 202, 130, 202, 72, 36, 18, 4)
    )


def test_presentation_question_productivity_claim_matches_transition_data() -> None:
    with (
        PRESENTATION_RESULTS
        / "public_protocol_common_facts_known_direct_transition.csv"
    ).open(encoding="utf-8-sig", newline="") as stream:
        [row] = list(csv.DictReader(stream))

    assert int(row["question_count"]) == 14
    assert int(row["patients_with_at_least_one_resolution_count"]) == 14
    assert int(row["resolved_after_one_question_count"]) == 14

    packet = PRESENTATION_PACKET.read_text(encoding="utf-8")
    assert "질문 14번 모두 적어도 한 시험의 판단을 바꿨다" in packet
    assert "물어도 어느 시험도 바뀌지 않은 질문 0회" in packet


def test_renderer_requires_route_choice_output(tmp_path: Path) -> None:
    missing_name = "route_choice_profile_results.csv"
    assert missing_name in REQUIRED_INPUTS
    for name in REQUIRED_INPUTS:
        if name != missing_name:
            (tmp_path / name).write_text("placeholder\n", encoding="utf-8")

    with pytest.raises(FileNotFoundError, match=missing_name):
        _validate_inputs(tmp_path)


def test_public_protocol_efficiency_uses_actions_actually_taken(
    tmp_path: Path,
) -> None:
    source = tmp_path / "public-protocol-policy-scale"
    source.mkdir()
    rows = [
        {
            "split": "heldout",
            "action_budget": 0,
            "policy_id": "no_questions",
            "patient_count": 30,
            "mean_final_status_matches_out_of_five": 14 / 15,
            "mean_action_count": 0,
        },
        {
            "split": "heldout",
            "action_budget": 1,
            "policy_id": "clarifytrial_rule_v1",
            "patient_count": 30,
            "mean_final_status_matches_out_of_five": 23 / 6,
            "mean_action_count": 1,
        },
        {
            "split": "heldout",
            "action_budget": 1,
            "policy_id": "random_order_expectation",
            "patient_count": 30,
            "mean_final_status_matches_out_of_five": 241 / 120,
            "mean_action_count": 1,
        },
    ]
    with (source / "policy-metrics.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    rows = _public_protocol_efficiency_rows(source)
    index = {
        (row["action_budget"], row["policy_id"]): row for row in rows
    }

    rule = index[(1, "clarifytrial_rule_v1")]
    random_order = index[(1, "random_order_expectation")]
    assert rule["mean_status_matches_out_of_five"] == pytest.approx(23 / 6)
    assert rule["mean_new_status_matches_out_of_five"] == pytest.approx(2.9)
    assert rule["new_status_matches_per_action_used"] == pytest.approx(2.9)
    assert random_order["new_status_matches_per_action_used"] == pytest.approx(
        1.075
    )


def test_burden_paired_rows_keep_base_patient_as_the_unit() -> None:
    inference = {
        "cluster_unit": "base_patient",
        "pair_count": 20,
        "mean_difference": 0.25,
        "bootstrap_95_ci": {"lower": 0.1, "upper": 0.4},
        "wins": 12,
        "ties": 6,
        "losses": 2,
        "two_sided_exact_sign_test_p": 0.05,
    }
    summary = {
        "mechanism_ablation": {
            "disallowed_path_filter": {
                "setting_pair_count": 80,
                "paired_inference": {"pending_trial_count": inference},
            }
        }
    }

    [row] = _burden_paired_rows(summary)

    assert row["independent_unit"] == "base_patient"
    assert row["independent_unit_count"] == 20
    assert row["repeated_setting_pair_count"] == 80


def test_live_model_smoke_is_labelled_as_one_connectivity_case(
    tmp_path: Path,
) -> None:
    live_dir = tmp_path / "live"
    deterministic_dir = tmp_path / "deterministic"
    live_dir.mkdir()
    deterministic_dir.mkdir()

    def payload(*, usage: dict[str, object]) -> dict[str, object]:
        return {
            "input": {"patient_id": "synthetic-01"},
            "result": {
                "usage": usage,
                "screening": {
                    "case_id": "integrated-ui:synthetic-01",
                    "final_decisions": [
                        {
                            "trial_id": "NCT00000001",
                            "candidate_status": "retain",
                            "confirmation_status": "confirmed",
                        }
                    ],
                    "action_history": [
                        {"agent_action": {"target_fact_id": "age_years"}}
                    ],
                },
            },
        }

    usage = {
        "call_count": 2,
        "input_tokens": 100,
        "output_tokens": 20,
        "thinking_tokens": 5,
        "total_tokens": 120,
        "by_role": {
            "matcher_judge": {"call_count": 1, "total_tokens": 70},
            "next_evidence": {"call_count": 1, "total_tokens": 50},
        },
    }
    (live_dir / "result.json").write_text(
        json.dumps(payload(usage=usage)),
        encoding="utf-8",
    )
    (deterministic_dir / "result.json").write_text(
        json.dumps(payload(usage={})),
        encoding="utf-8",
    )
    (live_dir / "trace.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "usage": {
                            "model_id": "gpt-test",
                            "effort": "medium",
                            "latency_ms": 1_000,
                        }
                    }
                ),
                json.dumps(
                    {
                        "event": "model_assessments_replaced",
                        "output": {
                            "corrections": [
                                {
                                    "criterion_id": "NCT00000001:criterion:01",
                                    "model": {"clinical_status": "unknown"},
                                    "applied": {"clinical_status": "supports"},
                                }
                            ]
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    [row] = _integrated_model_smoke_row(
        live_dir / "result.json",
        deterministic_dir / "result.json",
    )

    assert row["independent_unit"] == "single_synthetic_connectivity_case"
    assert row["independent_unit_count"] == 1
    assert row["model_call_count"] == 2
    assert row["total_tokens"] == 120
    assert row["total_model_latency_seconds"] == 1.0
    assert row["structured_rule_correction_count"] == 1
    assert row["structured_rule_correction_transitions"] == "unknown→supports(1)"
    assert row["final_trial_statuses_match_code_only"] is True
    assert row["question_fact_order_matches_code_only"] is True

    usage_without_matcher = {
        "call_count": 1,
        "input_tokens": 60,
        "output_tokens": 10,
        "thinking_tokens": 0,
        "total_tokens": 70,
        "by_role": {
            "next_evidence": {"call_count": 1, "total_tokens": 70},
        },
    }
    (live_dir / "result.json").write_text(
        json.dumps(payload(usage=usage_without_matcher)),
        encoding="utf-8",
    )
    (live_dir / "trace.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "usage": {
                            "model_id": "gpt-test",
                            "effort": "medium",
                            "latency_ms": 800,
                        }
                    }
                ),
                *[
                    json.dumps(
                        {"event": "structured_criteria_applied_without_model"}
                    )
                    for _ in range(3)
                ],
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    [row_without_matcher] = _integrated_model_smoke_row(
        live_dir / "result.json",
        deterministic_dir / "result.json",
    )

    assert row_without_matcher["matcher_judge_call_count"] == 0
    assert row_without_matcher["matcher_judge_tokens"] == 0
    assert row_without_matcher["next_evidence_call_count"] == 1
    assert row_without_matcher["total_model_latency_seconds"] == 0.8
    assert row_without_matcher["structured_model_skip_event_count"] == 3

    [change] = _model_role_routing_change_row(row, row_without_matcher)
    assert change["same_final_trial_statuses"] is True
    assert change["same_question_fact_order"] is True
    assert change["model_call_reduction_rate"] == 0.5
    assert change["token_reduction_rate"] == pytest.approx(5 / 12)
    assert change["model_latency_sum_reduction_rate"] == pytest.approx(0.2)
    assert change["before_structured_rule_correction_count"] == 1
    assert change["after_structured_rule_correction_count"] == 0
    assert change["after_structured_model_skip_event_count"] == 3


def test_statistical_unit_audit_does_not_turn_repeats_into_patients() -> None:
    rows = _statistical_unit_audit_rows(
        common_transition={
            "patient_count": 30,
            "trial_pair_count": 150,
            "initial_unresolved_trial_count": 22,
            "resolved_after_one_question_count": 14,
        },
        structural_summary={
            "structure_count": 1_800,
            "structure_state_count": 57_600,
            "policy_count": 12,
        },
        burden_summary={
            "mechanism_ablation": {
                "disallowed_path_filter": {
                    "base_patient_count": 20,
                    "setting_pair_count": 80,
                }
            }
        },
        route_choice_summary={
            "base_patient_count": 20,
            "masked_case_count": 40,
        },
        live_model_smoke={
            "independent_unit_count": 1,
            "model_call_count": 5,
        },
    )
    index = {row["analysis"]: row for row in rows}

    assert index["세 기본 항목 뒤 질문 1회 직접 전이"]["unit_count"] == 30
    assert index["세 기본 항목 뒤 질문 1회 직접 전이"][
        "repeated_measurement_count"
    ] == 150
    assert index["정보 연결 구조 B1~B3"]["unit_count"] == 1_800
    assert index["정보 연결 구조 B1~B3"][
        "repeated_measurement_count"
    ] == 2_073_600
    assert index["실제 모델 전체 화면 연결"][
        "repeated_measurement"
    ] == "같은 합성 사례의 변경 전·후 실행"
    assert index["실제 모델 전체 화면 연결"][
        "repeated_measurement_count"
    ] == 2
    assert index["같은 정보를 얻는 확인 방법 선택"]["unit_count"] == 20
    assert index["환자가 허용하지 않은 확인 방법 제거"]["unit_count"] == 20


def test_structural_reproduction_command_keeps_the_reported_seeds() -> None:
    guide = (
        ROOT
        / "docs"
        / "internal"
        / "CLARIFYTRIAL_V5_DEVELOPED_EXPERIMENT_GUIDE.md"
    ).read_text(encoding="utf-8")

    assert "--seed 20260830" in guide
    assert "--policy-seed 20260830" in guide


def test_main_presentation_script_fits_the_declared_time_without_rushed_slides() -> None:
    packet = PRESENTATION_PACKET.read_text(encoding="utf-8")
    main = packet.split("## 본편 17장", 1)[1].split("## 본편 시간 합", 1)[0]
    heading_pattern = re.compile(
        r"^### (\d+)\. .*? — (?:(\d+)분(?: (\d+)초)?|(\d+)초)$",
        re.MULTILINE,
    )
    headings = list(heading_pattern.finditer(main))

    total_seconds = 0
    slide_paces: list[float] = []
    for index, heading in enumerate(headings):
        end = headings[index + 1].start() if index + 1 < len(headings) else len(main)
        slide = main[heading.start() : end]
        seconds = int(heading.group(2) or 0) * 60 + int(
            heading.group(3) or heading.group(4) or 0
        )
        spoken = " ".join(
            line[2:] for line in slide.splitlines() if line.startswith("> ")
        )
        spoken_characters = len(re.sub(r"\s+", "", spoken))
        total_seconds += seconds
        slide_paces.append(spoken_characters / (seconds / 60))

    assert [int(item.group(1)) for item in headings] == list(range(1, 18))
    assert total_seconds == 1_020
    assert max(slide_paces) <= 285
    assert "발표 시작 1분 25초에 보이고 2분 45초 안에 끝난다" in packet


def test_presentation_planning_claim_matches_packaged_structure_results() -> None:
    with (PRESENTATION_RESULTS / "budget_policy_scores.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        policy_rows = list(csv.DictReader(stream))
    with (PRESENTATION_RESULTS / "subgroup_policy_differences.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        subgroup_rows = list(csv.DictReader(stream))
    with (PRESENTATION_RESULTS / "experiment_overview.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        overview_rows = list(csv.DictReader(stream))

    index = {
        (
            row["evaluation_distribution"],
            int(row["budget"]),
            row["policy_id"],
        ): float(row["mean_status_match_rate"])
        for row in policy_rows
        if row["suite"] == "synthetic_graph_stress"
    }
    overall_differences = [
        index[(distribution, budget, "clarifytrial_exact_coverage_v3")]
        - index[(distribution, budget, "clarifytrial_rule_v1")]
        for distribution in ("similar_heldout", "shifted_heldout")
        for budget in (2, 3)
    ]
    chain_differences = [
        float(row["difference"])
        for row in subgroup_rows
        if row["suite"] == "synthetic_graph_stress"
        and row["subgroup"] == "chain"
        and row["candidate_policy_id"] == "clarifytrial_exact_coverage_v3"
        and row["baseline_policy_id"] == "clarifytrial_rule_v1"
        and int(row["budget"]) in (2, 3)
    ]

    assert min(overall_differences) == pytest.approx(0.0119145432)
    assert max(overall_differences) == pytest.approx(0.0200166349)
    assert min(chain_differences) == pytest.approx(0.0322187630)
    assert max(chain_differences) == pytest.approx(0.0426649237)
    structural_calculations = sum(
        int(row["scenario_policy_evaluation_count"])
        for row in overview_rows
        if row["suite"] == "synthetic_graph_stress"
    )
    assert structural_calculations == 2_073_600

    packet = PRESENTATION_PACKET.read_text(encoding="utf-8")
    assert "전체 구조 평균 +1.2~+2.0%포인트" in packet
    assert "사슬형 구조 +3.2~+4.3%포인트" in packet
    assert "복잡한 계획의 추가 이득: 최대 +0.21%p" not in packet
    assert "구조마다 고정한 무작위 순서" not in packet
    assert "남은 순서를 함께 계산" not in packet
    assert "단계마다 남은 정보 중 하나를 무작위로 고르는 방법" in packet
    assert "앞으로 확인할 정보 조합" in packet
    assert "질문 순서를 미리 계산" not in packet
    assert "남은 횟수 안에 함께 볼 정보 묶음" not in packet
    assert "2,073,600번" in packet


def test_presentation_model_role_change_uses_the_same_prompt_before_run() -> None:
    with (PRESENTATION_RESULTS / "model_role_routing_change.csv").open(
        encoding="utf-8-sig", newline=""
    ) as stream:
        [row] = list(csv.DictReader(stream))

    assert int(row["before_model_call_count"]) == 5
    assert int(row["after_model_call_count"]) == 2
    assert int(row["before_total_tokens"]) == 72_659
    assert int(row["after_total_tokens"]) == 23_707
    assert float(row["before_total_model_latency_seconds"]) == pytest.approx(
        62.104
    )
    assert float(row["after_total_model_latency_seconds"]) == pytest.approx(
        43.485
    )

    packet = PRESENTATION_PACKET.read_text(encoding="utf-8")
    assert "같은 프롬프트와 합성 사례" in packet
    assert "72,659" in packet
    assert "62.104초" in packet
    assert "73,501" not in packet
    assert "64.946초" not in packet
