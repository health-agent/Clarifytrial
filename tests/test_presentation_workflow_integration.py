from __future__ import annotations

import json
from pathlib import Path

from clarifytrial.app import GeneralRunOptions, run_general_screening
from clarifytrial.llm import DeterministicWorkflowModel
from clarifytrial.settings import EpisodeSettings


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "general_screening"


def test_one_shared_answer_reaches_two_trials_and_the_final_guidance(
    tmp_path: Path,
) -> None:
    outcome = run_general_screening(
        options=GeneralRunOptions(
            patient_path=EXAMPLE / "patient.json",
            trials_path=EXAMPLE / "trials.jsonl",
            answers_path=EXAMPLE / "presentation-answers.json",
            output_dir=tmp_path,
            settings=EpisodeSettings(
                max_external_actions=3,
                max_selective_reviews=1,
                max_cycles=12,
            ),
        ),
        model=DeterministicWorkflowModel(),
        model_label="deterministic-workflow",
        medical_disclaimer="학생 과제용 합성 실행입니다.",
        write=lambda _: None,
    )

    result = json.loads(outcome.result_path.read_text(encoding="utf-8"))
    screening = result["screening"]
    assert len(screening["action_history"]) == 1
    action = screening["action_history"][0]
    assert action["agent_action"]["target_fact_id"] == "recent-hba1c"
    assert action["agent_action"]["related_criterion_ids"] == [
        "NCT-SYNTH-A:inclusion:hba1c",
        "NCT-SYNTH-B:inclusion:hba1c",
    ]
    assert action["acquisition_decision"]["policy_id"] == "patient_adaptive"
    ordering = action["acquisition_decision"]["decision_trace"][
        "applied_ordering_rule"
    ]
    assert ordering[:2] == ["affected_trials:max", "affected_criteria:max"]
    assert "exact_coverage_choice:max" not in ordering

    final_by_trial = {
        item["trial_id"]: item for item in screening["final_decisions"]
    }
    assert final_by_trial["NCT-SYNTH-A"]["confirmation_status"] == "ineligible"
    assert final_by_trial["NCT-SYNTH-B"]["confirmation_status"] == "confirmed"
    for decision in final_by_trial.values():
        assert decision["criterion_assessments"][0]["evidence_ids"] == [
            "presentation-official-hba1c"
        ]

    guidance = screening["guidance"]
    assert guidance["patient_input_status"] == "absent"
    assert "preference_mode" in guidance["defaulted_fields"]
    assert guidance["trial_groups"]["confirmed_trial_ids"] == ["NCT-SYNTH-B"]
    assert guidance["trial_groups"]["removed_trial_ids"] == ["NCT-SYNTH-A"]
