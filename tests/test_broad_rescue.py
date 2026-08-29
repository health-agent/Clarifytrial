from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from clarifytrial.app.evaluation import run_full_workflow_evaluation
from clarifytrial.datasets.broad_rescue import (
    audit_broad_rescue_dataset,
    build_broad_rescue_dataset,
)
from clarifytrial.llm import DeterministicWorkflowModel
from clarifytrial.reporting import (
    build_final_evaluation_readiness,
    build_research_report,
)
from clarifytrial.ui import build_integrated_ui_fixture


ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "configs/broad_rescue_maturity_v1.json"


def test_broad_rescue_dataset_is_reproducible_and_not_all_positive(
    tmp_path: Path,
) -> None:
    result = build_broad_rescue_dataset(
        config_path=CONFIG,
        output_dir=tmp_path,
    )
    audit = audit_broad_rescue_dataset(
        config_path=CONFIG,
        trial_set_path=result["trial_set"],
        patient_pairs_path=result["patient_pairs"],
    )

    assert audit["passed"] is True
    assert audit["group_count"] == 10
    assert audit["trial_count"] == 50
    assert audit["patient_count"] == 50
    assert audit["patient_trial_pair_count"] == 250
    assert audit["complete_confirmed_candidate_count"] == 154
    assert audit["complete_ineligible_count"] == 96
    assert set(audit["acquisition_mode_counts"]) == {
        "existing_official_result",
        "internal_record",
        "new_noninvasive_test",
        "patient_report",
    }
    document = json.loads(Path(result["patient_pairs"]).read_text(encoding="utf-8"))
    assert Counter(len(item["pivotal_fact_codes"]) for item in document["pairs"]) == {
        1: 10,
        2: 10,
        3: 20,
        5: 10,
    }


def test_broad_case_runs_connected_rescue_metrics_and_readiness_report(
    tmp_path: Path,
) -> None:
    dataset_dir = tmp_path / "dataset"
    paths = build_broad_rescue_dataset(
        config_path=CONFIG,
        output_dir=dataset_dir,
    )
    fixture = build_integrated_ui_fixture(
        trial_set_path=paths["trial_set"],
        patient_pairs_path=paths["patient_pairs"],
        generation_config_path=CONFIG,
        patient_id="broad-pulmonary_fibrosis-03",
    )
    assert len(fixture.screening_case.trials) == 5
    assert len(fixture.screening_case.evidence_requests) == 5
    assert any(
        option.acquisition_mode.value == "new_noninvasive_test"
        and option.requires_patient_choice
        and option.requires_clinician_authorization
        for option in fixture.screening_case.acquisition_options
    )

    workflow_dir = tmp_path / "workflow"
    summary = run_full_workflow_evaluation(
        trial_set_path=paths["trial_set"],
        patient_pairs_path=paths["patient_pairs"],
        generation_config_path=CONFIG,
        destination=workflow_dir,
        model=DeterministicWorkflowModel(),
        model_label="deterministic-workflow",
        concurrency=2,
        include_unavailable_scenario=True,
        include_patient_choice_scenario=True,
        approve_synthetic_actions=True,
        progress=lambda _: None,
    )
    assert summary["protocol_id"] == "clarifytrial-full-workflow-evaluation-v5"
    assert summary["agent_architecture"] == "rules_only"
    current = next(
        item for item in summary["arm_metrics"] if item["arm"] == "clarifytrial"
    )
    uncertainty = current["cluster_uncertainty"]["trial_status_recovery"]
    assert uncertainty["cluster_unit"] == "patient"
    assert uncertainty["disease_group_count"] == 10
    assert current["rescue_opportunity_count"] > 0
    assert current["candidate_preservation_count"] == current[
        "rescue_opportunity_count"
    ]
    assert current["new_test_count"] > 0
    unavailable = next(
        item
        for item in summary["unavailable_answer_metrics"]
        if item["arm"] == "clarifytrial"
    )
    assert unavailable["repeated_fact_action_count"] == 0
    declined = next(
        item
        for item in summary["patient_declines_new_tests_metrics"]
        if item["arm"] == "clarifytrial"
    )
    assert declined["new_test_count"] == 0
    assert declined["additional_visit_count"] == 0

    report = build_research_report(
        destination=tmp_path / "report",
        workflow_path=workflow_dir / "summary.json",
    )
    report_text = Path(report["report"]).read_text(encoding="utf-8")
    assert "처음에는 보이지 않던 실제 후보를 추가 확인으로 확정한 결과" in report_text

    readiness = build_final_evaluation_readiness(
        trial_set_path=paths["trial_set"],
        patient_pairs_path=paths["patient_pairs"],
        workflow_summary_path=workflow_dir / "summary.json",
        output_dir=tmp_path / "readiness",
    )
    assert readiness["software_ready_for_external_model_evaluation"] is False
    assert readiness["independent_performance_claim_ready"] is False
    failed_gate_ids = {
        item["gate_id"] for item in readiness["gates"] if not item["passed"]
    }
    assert failed_gate_ids == {"G6", "G7", "G8", "G9"}
