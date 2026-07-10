"""Tests for the deterministic end-to-end dry-run demo."""

import os
from pathlib import Path

import pytest

from models import FinalOutput
from scripts.run_end_to_end_demo import SUMMARY_PATH, main

API_KEY_ENV_VARS = [
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "GOOGLE_API_KEY",
]


@pytest.fixture(scope="module")
def demo_output() -> FinalOutput:
    # Strip any API-key env vars up front: the demo must not need them.
    for var in API_KEY_ENV_VARS:
        os.environ.pop(var, None)
    return main()


def test_demo_runs_without_error_and_returns_final_output(demo_output):
    assert isinstance(demo_output, FinalOutput)
    assert "not medical advice" in demo_output.medical_disclaimer.lower()


def test_demo_writes_summary_file(demo_output):
    assert SUMMARY_PATH.is_file()
    content = SUMMARY_PATH.read_text(encoding="utf-8")
    assert "SYNTHETIC" in content
    assert "Ranked recommendations" in content


def test_demo_includes_at_least_one_missing_variable(demo_output):
    assert len(demo_output.pending_questions) >= 1
    keys = {q.missing_variable_key for q in demo_output.pending_questions}
    assert "ecog_performance_status" in keys


def test_demo_includes_trial_recommendations(demo_output):
    assert len(demo_output.trial_recommendations) >= 1
    ranks = [rec.rank for rec in demo_output.trial_recommendations]
    assert ranks == sorted(ranks)


def test_demo_requires_no_api_keys(demo_output):
    # The fixture removed all API-key env vars before running the demo and
    # it still completed; double-check none were set during the run.
    for var in API_KEY_ENV_VARS:
        assert os.environ.get(var) is None
