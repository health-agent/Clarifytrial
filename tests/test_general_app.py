from __future__ import annotations

import json
from pathlib import Path

from clarifytrial.app import GeneralRunOptions, ScreeningSession, run_general_screening
from clarifytrial.app.loaders import (
    load_general_patient,
    load_structured_trials,
    prepare_general_case,
)
from clarifytrial.llm import DeterministicWorkflowModel
from clarifytrial.settings import EpisodeSettings


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "general_screening"


def _options(output: Path, *, resume: Path | None = None) -> GeneralRunOptions:
    return GeneralRunOptions(
        patient_path=EXAMPLE / "patient.json",
        trials_path=EXAMPLE / "trials.jsonl",
        answers_path=EXAMPLE / "answers.json" if resume is None else None,
        output_dir=output,
        resume_path=resume,
        settings=EpisodeSettings(
            max_external_actions=3,
            max_selective_reviews=1,
            max_cycles=12,
        ),
    )


def test_general_files_search_and_run_without_fixed_fixture(tmp_path: Path) -> None:
    patient = load_general_patient(EXAMPLE / "patient.json")
    trials = load_structured_trials(EXAMPLE / "trials.jsonl")
    prepared = prepare_general_case(patient, trials)

    assert prepared.trial_pool_count == 3
    assert [item.trial_id for item in prepared.case.trials] == [
        "NCT-SYNTH-A",
        "NCT-SYNTH-B",
    ]

    outcome = run_general_screening(
        options=_options(tmp_path),
        model=DeterministicWorkflowModel(),
        model_label="deterministic-workflow",
        medical_disclaimer="학생 과제용 실험 결과입니다.",
        write=lambda _: None,
    )

    assert outcome.paused is False
    result = json.loads(outcome.result_path.read_text(encoding="utf-8"))
    assert result["screening"]["stop_reason"] == "all_trials_resolved"
    assert result["usage"]["call_count"] == 3
    assert all(
        item["confirmation_status"] == "confirmed"
        for item in result["screening"]["final_decisions"]
    )


def test_general_search_accepts_a_new_condition_without_code_change(
    tmp_path: Path,
) -> None:
    patient = json.loads((EXAMPLE / "patient.json").read_text(encoding="utf-8"))
    patient["case_id"] = "new-condition-case"
    patient["search_conditions"] = ["rare synthetic condition"]
    patient_path = tmp_path / "patient.json"
    patient_path.write_text(json.dumps(patient), encoding="utf-8")
    rows = [json.loads(line) for line in (EXAMPLE / "trials.jsonl").read_text(encoding="utf-8").splitlines()]
    for row in rows[:2]:
        row["conditions"] = ["rare synthetic condition"]
        row["title"] = "Rare synthetic condition study"
    trial_path = tmp_path / "trials.jsonl"
    trial_path.write_text(
        "\n".join(json.dumps(item) for item in rows) + "\n",
        encoding="utf-8",
    )

    prepared = prepare_general_case(
        load_general_patient(patient_path),
        load_structured_trials(trial_path),
    )

    assert len(prepared.case.trials) == 2


def test_interactive_session_pauses_and_resumes(tmp_path: Path) -> None:
    first = GeneralRunOptions(
        patient_path=EXAMPLE / "patient.json",
        trials_path=EXAMPLE / "trials.jsonl",
        output_dir=tmp_path,
        settings=EpisodeSettings(
            max_external_actions=3,
            max_selective_reviews=1,
            max_cycles=12,
        ),
    )
    paused = run_general_screening(
        options=first,
        model=DeterministicWorkflowModel(),
        model_label="deterministic-workflow",
        medical_disclaimer="학생 과제용 실험 결과입니다.",
        read=lambda _: "quit",
        write=lambda _: None,
    )
    assert paused.paused is True
    session = ScreeningSession.model_validate_json(
        paused.session_path.read_text(encoding="utf-8")
    )
    assert session.completed is False
    assert session.action_count == 0

    answer = json.dumps(
        {
            "statement": "Official HbA1c result was 6.4 percent.",
            "concept": "hba1c",
            "value": 6.4,
            "unit": "%",
            "event_date": "2026-08-20",
        }
    )
    resumed_options = GeneralRunOptions(
        patient_path=EXAMPLE / "patient.json",
        trials_path=EXAMPLE / "trials.jsonl",
        output_dir=tmp_path,
        settings=first.settings,
        resume_path=paused.session_path,
    )
    resumed = run_general_screening(
        options=resumed_options,
        model=DeterministicWorkflowModel(),
        model_label="deterministic-workflow",
        medical_disclaimer="학생 과제용 실험 결과입니다.",
        read=lambda _: answer,
        write=lambda _: None,
    )
    assert resumed.paused is False
    final_session = ScreeningSession.model_validate_json(
        resumed.session_path.read_text(encoding="utf-8")
    )
    assert final_session.completed is True
    assert final_session.action_count == 1
    assert final_session.revealed_fact_ids == ["recent-hba1c"]
    assert all(
        item.confirmation_status.value == "confirmed"
        for item in final_session.result.final_decisions
    )
