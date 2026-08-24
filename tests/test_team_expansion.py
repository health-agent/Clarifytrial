from __future__ import annotations

import json
from pathlib import Path

from clarifytrial.datasets.team_expansion import select_team_evaluation_trials


def _trial(trial_id: str, condition: str, eligibility: str) -> dict:
    return {
        "nct_id": trial_id,
        "title": f"{condition} {trial_id}",
        "conditions": [condition],
        "brief_summary": "Synthetic public-corpus test row.",
        "eligibility_text": eligibility,
        "sex": "ALL",
        "minimum_age": "18 Years",
        "maximum_age": None,
        "overall_status": "RECRUITING",
        "phase": [],
    }


def test_expansion_selector_builds_unique_diverse_group_pool(tmp_path: Path) -> None:
    corpus = tmp_path / "trials.jsonl"
    corpus.write_text(
        "".join(
            json.dumps(item) + "\n"
            for item in [
                _trial("NCT-A", "Disease A", "Age at least 18 years."),
                _trial("NCT-B", "Disease A", "Pregnancy is excluded."),
                _trial("NCT-C", "Disease B", "MRI within 4 weeks."),
                _trial("NCT-D", "Disease B", "Prior drug treatment is required."),
            ]
        ),
        encoding="utf-8",
    )
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "protocol_id": "test-expansion",
                "disease_groups": [
                    {
                        "group_id": "a",
                        "group_label": "A",
                        "condition_aliases": ["disease a"],
                        "target_count": 2,
                    },
                    {
                        "group_id": "b",
                        "group_label": "B",
                        "condition_aliases": ["disease b"],
                        "target_count": 2,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    output = tmp_path / "selection.json"

    result = select_team_evaluation_trials(
        corpus_path=corpus,
        config_path=config,
        destination=output,
    )

    assert result["group_count"] == 2
    assert result["selected_trial_count"] == 4
    assert len({item["nct_id"] for item in result["selected_trials"]}) == 4
    assert result["criterion_category_counts"]["numeric_threshold"] >= 1
    assert result["criterion_category_counts"]["time_window"] >= 1
    assert output.is_file()
