from __future__ import annotations

import json
from pathlib import Path

import pytest

from clarifytrial.preparation import (
    TeamTrialCandidateSearch,
    inspect_team_trial_corpus,
    team_trial_sources,
)
from clarifytrial.preparation.team_trials import TEAM_TRIALS_URL


def _row(trial_id: str, status: str, condition: str) -> dict:
    return {
        "nct_id": trial_id,
        "title": f"{condition} study {trial_id}",
        "conditions": [condition],
        "brief_summary": f"Research about {condition}.",
        "eligibility_text": "Inclusion Criteria:\n* Age at least 18 years",
        "sex": "ALL",
        "minimum_age": "18 Years",
        "maximum_age": None,
        "overall_status": status,
        "phase": [],
    }


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(item) + "\n" for item in rows),
        encoding="utf-8",
    )


def test_team_corpus_filters_non_enrolling_trials_before_search(
    tmp_path: Path,
) -> None:
    path = tmp_path / "trials.jsonl"
    _write(
        path,
        [
            _row("NCT-A", "RECRUITING", "Bladder Cancer"),
            _row("NCT-B", "COMPLETED", "Bladder Cancer"),
            _row("NCT-C", "NOT_YET_RECRUITING", "Migraine"),
        ],
    )

    summary = inspect_team_trial_corpus(path)
    sources = team_trial_sources(path)
    hits = TeamTrialCandidateSearch(path).search(["bladder cancer"], top_k=5)

    assert summary.row_count == 3
    assert summary.included_trial_count == 2
    assert {item.trial_id for item in sources} == {"NCT-A", "NCT-C"}
    assert [item.source.trial_id for item in hits] == ["NCT-A"]
    assert hits[0].retrieval_method == "team-jsonl-bm25"
    assert all(
        item.source_location.startswith(TEAM_TRIALS_URL)
        for item in sources
    )
    assert str(tmp_path.resolve()) not in json.dumps(
        [item.model_dump(mode="json") for item in sources]
    )


def test_team_corpus_rejects_repeated_trial_ids(tmp_path: Path) -> None:
    path = tmp_path / "trials.jsonl"
    row = _row("NCT-A", "RECRUITING", "Bladder Cancer")
    _write(path, [row, row])

    with pytest.raises(ValueError, match="repeats"):
        inspect_team_trial_corpus(path)
