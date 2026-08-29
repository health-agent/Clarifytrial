from __future__ import annotations

import json
from pathlib import Path

from clarifytrial.interactive.shared_fact_report import (
    build_shared_fact_report,
    is_trial_specific_proxy_fact,
    write_shared_fact_report,
)


ROOT = Path(__file__).resolve().parents[1]
TRIAL_SET = ROOT / "data/public_protocol_benchmark_v1/trial_set.json"


def _small_trial_set() -> dict:
    trials = [
        {"group_id": "group", "nct_id": f"NCT{i:08d}"}
        for i in range(1, 6)
    ]
    criteria = []
    for trial in trials:
        criteria.append(
            {
                "group_id": "group",
                "nct_id": trial["nct_id"],
                "fact_code": "age_years",
            }
        )
    criteria.extend(
        [
            {
                "group_id": "group",
                "nct_id": "NCT00000001",
                "fact_code": "age_years",
            },
            {
                "group_id": "group",
                "nct_id": "NCT00000001",
                "fact_code": "active_infection",
            },
            {
                "group_id": "group",
                "nct_id": "NCT00000002",
                "fact_code": "active_infection",
            },
            {
                "group_id": "group",
                "nct_id": "NCT00000001",
                "fact_code": "nct00000001_source_line_4",
            },
        ]
    )
    return {
        "groups": [{"group_id": "group", "group_label": "시험 질환"}],
        "trials": trials,
        "criteria": criteria,
    }


def test_proxy_fact_classification_is_narrow() -> None:
    assert is_trial_specific_proxy_fact("nct01234567_source_line_4")
    assert is_trial_specific_proxy_fact("NCT01234567_logic_line_12")
    assert not is_trial_specific_proxy_fact("age_years")
    assert not is_trial_specific_proxy_fact("nct01234567_custom_value")


def test_shared_fact_counts_distinct_trials_not_repeated_criteria() -> None:
    report = build_shared_fact_report(_small_trial_set())
    group = report["groups"][0]
    age = next(row for row in group["facts"] if row["fact_code"] == "age_years")

    assert age["criterion_count"] == 6
    assert age["trial_count"] == 5
    assert group["unique_group_specific_fact_count"] == 3
    assert group["reusable_normalized_fact_count"] == 2
    assert group["trial_specific_proxy_fact_count"] == 1
    assert group["facts_used_by_at_least_2_trials"] == 2
    assert group["facts_used_by_at_least_3_trials"] == 1
    assert group["facts_used_by_all_trials"] == 1
    assert group["criteria_whose_fact_is_used_by_at_least_2_trials"] == 8
    assert group["share_of_criteria_with_a_cross_trial_fact"] == 8 / 9
    assert report["overall"]["shared_criterion_count_by_fact_code"] == {
        "active_infection": 2,
        "age_years": 6,
    }


def test_public_benchmark_report_is_complete_and_writable(tmp_path: Path) -> None:
    report = write_shared_fact_report(
        trial_set_path=TRIAL_SET,
        output_dir=tmp_path,
    )

    assert report["scope"]["group_count"] == 10
    assert report["scope"]["trial_count"] == 50
    assert report["scope"]["criterion_count"] == 202
    assert len(report["groups"]) == 10
    assert all(group["trial_count"] == 5 for group in report["groups"])
    assert (
        report["overall"]["unique_group_specific_fact_count"]
        == report["overall"]["reusable_normalized_group_fact_count"]
        + report["overall"]["trial_specific_proxy_group_fact_count"]
    )
    assert report["overall"]["facts_used_by_at_least_2_trials"] > 0
    assert report["overall"]["facts_used_by_all_5_trials"] > 0
    assert 0 < report["overall"]["share_of_criteria_with_a_cross_trial_fact"] < 1
    composition = report["overall"]["shared_criterion_count_by_fact_code"]
    assert composition["age_years"] == 72
    assert composition["pregnancy_or_lactation"] == 36
    assert composition["active_serious_infection"] == 18

    written = json.loads(
        (tmp_path / "shared-fact-report.json").read_text(encoding="utf-8")
    )
    assert written == report
    markdown = (tmp_path / "shared-fact-report.md").read_text(encoding="utf-8")
    assert "공개 임상시험 50건" in markdown
    assert "실제 진료" in markdown
