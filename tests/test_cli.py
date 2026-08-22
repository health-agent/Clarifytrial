from __future__ import annotations

import json
from pathlib import Path

import pytest

from clarifytrial.cli import _read_env_value, export_schemas, main, run_example
from clarifytrial.workflow import EpisodeRunner


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
CASE_DIR = REPOSITORY_ROOT / "examples" / "stale_lab"


def _read_json(path: Path):
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


def test_stale_lab_cli_runs_real_episode_without_external_api(tmp_path: Path) -> None:
    output = tmp_path / "run"

    exit_code = main(
        [
            "run-example",
            "--case",
            str(CASE_DIR),
            "--output",
            str(output),
        ]
    )

    assert exit_code == 0
    payload = _read_json(output / "result.json")
    assert payload["run_mode"] == "scripted_local_dry_run"
    assert payload["external_api_calls"] == 0
    assert "연구용 시제품" in payload["disclaimer"]
    assert payload["episode"]["stop_reason"] == "confirmed"
    assert payload["episode"]["final_decision"]["candidate_status"] == "retain"
    assert payload["episode"]["final_decision"]["confirmation_status"] == (
        "confirmed"
    )
    assert payload["episode"]["action_history"][0]["action"] == (
        "REQUEST_VERIFICATION"
    )
    assert payload["initial_state_score"]["candidate_correct"] is True
    assert payload["initial_state_score"]["confirmation_correct"] is True
    assert payload["initial_state_score"]["action_acceptable"] is True
    assert payload["model_calls_by_role"] == {
        "coordinator": 4,
        "matcher_judge": 2,
        "next_evidence": 1,
    }

    trace_lines = (output / "trace.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in trace_lines]
    assert [event["sequence"] for event in events] == list(
        range(1, len(events) + 1)
    )
    assert any(event["actor"] == "decision_rules" for event in events)
    assert any(event["actor"] == "mechanical_checks" for event in events)
    assert any(event["actor"] == "synthetic_information_tools" for event in events)


def test_case_keeps_visible_input_hidden_answer_and_gold_separate() -> None:
    system_input = (CASE_DIR / "system_input.json").read_text(encoding="utf-8")
    public_questions = (CASE_DIR / "public_questions.json").read_text(
        encoding="utf-8"
    )
    hidden_answers = (CASE_DIR / "hidden_answers.json").read_text(encoding="utf-8")
    gold = (CASE_DIR / "gold_initial.json").read_text(encoding="utf-8")

    assert "platelets-2026-08-18" not in system_input
    assert "platelets-2026-08-18" not in public_questions
    assert "platelets-2026-08-18" in hidden_answers
    assert '"candidate_status"' not in system_input
    assert '"candidate_status"' not in public_questions
    assert '"candidate_status"' in gold


def test_run_example_reads_gold_only_after_episode_finishes(
    tmp_path: Path, monkeypatch
) -> None:
    original_open = Path.open
    original_run = EpisodeRunner.run
    episode_finished = False
    gold_opened = False

    def tracked_run(*args, **kwargs):
        nonlocal episode_finished
        result = original_run(*args, **kwargs)
        episode_finished = True
        return result

    def guarded_open(path: Path, *args, **kwargs):
        nonlocal gold_opened
        if path.name == "gold_initial.json":
            if not episode_finished:
                raise AssertionError("gold labels were opened during the episode")
            gold_opened = True
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(EpisodeRunner, "run", tracked_run)
    monkeypatch.setattr(Path, "open", guarded_open)
    result_path = run_example(CASE_DIR, tmp_path / "run")

    assert gold_opened is True
    assert result_path.is_file()


def test_export_schemas_writes_parseable_key_contracts(tmp_path: Path) -> None:
    paths = export_schemas(tmp_path / "schemas")

    assert {path.name for path in paths} == {
        "episode-case.schema.json",
        "episode-result.schema.json",
        "patient-state.schema.json",
        "trial-decision.schema.json",
        "public-fact-request.schema.json",
        "hidden-fact-answer.schema.json",
        "decision-gold.schema.json",
        "acquisition-option.schema.json",
        "patient-burden-profile.schema.json",
        "guidance-output.schema.json",
        "recommendation-views.schema.json",
        "patient-screening-case.schema.json",
        "patient-screening-result.schema.json",
        "natural-screening-request.schema.json",
        "natural-screening-result.schema.json",
    }
    assert all(_read_json(path)["type"] == "object" for path in paths)


def test_natural_screening_command_requires_explicit_model_confirmation(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as captured:
        main(
            [
                "run-natural-screening",
                "--request",
                str(tmp_path / "request.json"),
                "--trial-sources",
                str(tmp_path / "trials.json"),
                "--hidden-answers",
                str(tmp_path / "answers.json"),
                "--output",
                str(tmp_path / "run"),
            ]
        )
    assert captured.value.code == 2


def test_live_trialgpt_command_requires_explicit_confirmation(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as captured:
        main(
            [
                "run-trialgpt-pilot",
                "--raw-jsonl",
                str(tmp_path / "rows.jsonl"),
                "--sigir-corpus",
                str(tmp_path / "corpus.jsonl"),
                "--output",
                str(tmp_path / "run"),
                "--api-key-env-file",
                str(tmp_path / "key.env"),
            ]
        )

    assert captured.value.code == 2


def test_natural_record_structure_command_requires_subscription_confirmation(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as captured:
        main(
            [
                "run-natural-record-structure-evaluation",
                "--records",
                str(tmp_path / "records.json"),
                "--output",
                str(tmp_path / "result.json"),
            ]
        )

    assert captured.value.code == 2


def test_live_trialgpt_experiment_requires_explicit_confirmation(
    tmp_path: Path,
) -> None:
    with pytest.raises(SystemExit) as captured:
        main(
            [
                "run-trialgpt-experiment",
                "--raw-jsonl",
                str(tmp_path / "rows.jsonl"),
                "--sigir-corpus",
                str(tmp_path / "corpus.jsonl"),
                "--output",
                str(tmp_path / "run"),
                "--api-key-env-file",
                str(tmp_path / "key.env"),
                "--variant",
                "calibrated",
            ]
        )

    assert captured.value.code == 2


def test_env_value_reader_supports_existing_key_file_without_copying_it(
    tmp_path: Path,
) -> None:
    source = tmp_path / "key.env"
    source.write_text("# local only\nAPI_KEY='secret-value'\n", encoding="utf-8")

    assert _read_env_value(source, "API_KEY") == "secret-value"
