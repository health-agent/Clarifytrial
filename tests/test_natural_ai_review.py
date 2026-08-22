from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

import clarifytrial.cli as cli_module
from clarifytrial.datasets.natural_ai_review import (
    AiCriterionReviewBatch,
    build_conservative_natural_ai_gold,
    run_natural_evaluation_ai_review,
    run_natural_evaluation_max_resolution,
    validate_ai_review_batch,
)
from clarifytrial.llm import ScriptedStructuredModel


def _trial() -> dict:
    return {
        "group_id": "type_2_diabetes",
        "nct_id": "NCT90000001",
        "title": "Synthetic objective criteria",
        "conditions": ["Type 2 Diabetes"],
        "criterion_candidates": [
            {
                "candidate_id": "NCT90000001:candidate:001",
                "section_hint": "inclusion",
                "line_number": 3,
                "start_char": 20,
                "end_char": 38,
                "source_text": "Age must be ≥18 years.",
            },
            {
                "candidate_id": "NCT90000001:candidate:002",
                "section_hint": "inclusion",
                "line_number": 4,
                "start_char": 39,
                "end_char": 71,
                "source_text": "Patients must meet all criteria:",
            },
            {
                "candidate_id": "NCT90000001:candidate:003",
                "section_hint": "exclusion",
                "line_number": 7,
                "start_char": 90,
                "end_char": 113,
                "source_text": "Current use of insulin.",
            },
        ],
    }


def _numeric_review() -> dict:
    return {
        "candidate_id": "NCT90000001:candidate:001",
        "decision": "include",
        "confidence": "high",
        "reason_code": "objective_numeric",
        "annotations": [
            {
                "fact_code": "age",
                "fact_description": "Age in years",
                "criterion_summary": "Age is at least 18 years",
                "expected_value": None,
                "operator": "gte",
                "threshold": 18,
                "unit": "years",
            }
        ],
        "note": None,
    }


def _categorical_review() -> dict:
    return {
        "candidate_id": "NCT90000001:candidate:003",
        "decision": "include",
        "confidence": "high",
        "reason_code": "objective_explicit_state",
        "annotations": [
            {
                "fact_code": "current_insulin_use",
                "fact_description": "Current insulin use",
                "criterion_summary": "Currently uses insulin",
                "expected_value": "present",
                "operator": None,
                "threshold": None,
                "unit": None,
            }
        ],
        "note": None,
    }


def _heading_review(decision: str) -> dict:
    return {
        "candidate_id": "NCT90000001:candidate:002",
        "decision": decision,
        "confidence": "low" if decision == "uncertain" else "high",
        "reason_code": (
            "other" if decision == "uncertain" else "heading_or_context"
        ),
        "annotations": [],
        "note": "List introduction",
    }


def test_two_pass_ai_review_writes_preliminary_outputs_and_reuses_checkpoints(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps({"trials": [_trial()]}, ensure_ascii=False),
        encoding="utf-8",
    )
    first = {
        "reviews": [_numeric_review(), _heading_review("uncertain"), _categorical_review()]
    }
    audited = {
        "reviews": [_numeric_review(), _heading_review("exclude"), _categorical_review()]
    }
    model = ScriptedStructuredModel(
        {
            "natural_criterion_ai_review": lambda _: first,
            "natural_criterion_ai_audit": lambda _: audited,
        }
    )
    review_output = tmp_path / "review.json"
    gold_output = tmp_path / "gold.json"
    checkpoints = tmp_path / "checkpoints"

    result = run_natural_evaluation_ai_review(
        source_path=source,
        review_output_path=review_output,
        gold_output_path=gold_output,
        checkpoint_dir=checkpoints,
        model=model,
        model_id="scripted-local",
        effort="max",
        concurrency=1,
    )

    assert result["source_line_count"] == 3
    assert result["criterion_count"] == 2
    assert result["changed_after_audit_count"] == 1
    review = json.loads(review_output.read_text(encoding="utf-8"))
    gold = json.loads(gold_output.read_text(encoding="utf-8"))
    assert review["status"] == "preliminary_single_ai_double_pass"
    assert review["decision_counts"] == {
        "include": 2,
        "exclude": 1,
    }
    assert gold["status"] == "preliminary_single_ai_reviewed_gold"
    assert gold["criterion_count"] == 2

    review_output.unlink()
    gold_output.unlink()
    no_call_model = ScriptedStructuredModel(
        {
            "natural_criterion_ai_review": lambda _: pytest.fail("must reuse first pass"),
            "natural_criterion_ai_audit": lambda _: pytest.fail("must reuse audit pass"),
        }
    )
    reused = run_natural_evaluation_ai_review(
        source_path=source,
        review_output_path=review_output,
        gold_output_path=gold_output,
        checkpoint_dir=checkpoints,
        model=no_call_model,
        model_id="scripted-local",
        effort="max",
        concurrency=1,
    )
    assert reused["criterion_count"] == 2


def test_numeric_value_not_supported_by_source_is_downgraded() -> None:
    invalid = _numeric_review()
    invalid["annotations"][0]["threshold"] = 99
    batch = AiCriterionReviewBatch.model_validate(
        {
            "reviews": [
                invalid,
                _heading_review("exclude"),
                _categorical_review(),
            ]
        }
    )

    checked = validate_ai_review_batch(_trial(), batch)

    assert checked.reviews[0].decision == "uncertain"
    assert checked.reviews[0].annotations == []


def test_ai_review_refuses_to_overwrite_existing_outputs(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"trials": [_trial()]}), encoding="utf-8")
    review_output = tmp_path / "review.json"
    review_output.write_text("{}", encoding="utf-8")

    with pytest.raises(FileExistsError):
        run_natural_evaluation_ai_review(
            source_path=source,
            review_output_path=review_output,
            gold_output_path=tmp_path / "gold.json",
            checkpoint_dir=tmp_path / "checkpoint",
            model=ScriptedStructuredModel({}),
            model_id="scripted-local",
            effort="max",
            concurrency=1,
        )


def test_invalid_model_output_is_deferred_instead_of_losing_other_work(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"trials": [_trial()]}), encoding="utf-8")
    model = ScriptedStructuredModel(
        {
            "natural_criterion_ai_review": lambda _: {"reviews": []},
            "natural_criterion_ai_audit": lambda _: {"reviews": []},
        }
    )

    result = run_natural_evaluation_ai_review(
        source_path=source,
        review_output_path=tmp_path / "review.json",
        gold_output_path=tmp_path / "gold.json",
        checkpoint_dir=tmp_path / "checkpoints",
        model=model,
        model_id="scripted-local",
        effort="medium",
        concurrency=1,
    )

    assert result["criterion_count"] == 0
    assert result["decision_counts"] == {"uncertain": 3}
    assert result["usage"]["failed_model_calls"] == 2


def test_maximum_resolution_only_reviews_uncertain_or_medium_lines(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"trials": [_trial()]}), encoding="utf-8")
    base = {
        "model": "gpt-5.6-sol",
        "effort": "medium",
        "usage": {"model_calls": 2},
        "reviews": [],
    }
    for item in [_numeric_review(), _heading_review("exclude"), _categorical_review()]:
        if item["candidate_id"].endswith("003"):
            item["confidence"] = "medium"
        candidate = next(
            row
            for row in _trial()["criterion_candidates"]
            if row["candidate_id"] == item["candidate_id"]
        )
        base["reviews"].append(
            {
                "group_id": "type_2_diabetes",
                "nct_id": "NCT90000001",
                "candidate_id": item["candidate_id"],
                "section": candidate["section_hint"],
                "line_number": candidate["line_number"],
                "start_char": candidate["start_char"],
                "end_char": candidate["end_char"],
                "source_text": candidate["source_text"],
                **{key: value for key, value in item.items() if key != "candidate_id"},
            }
        )
    base_path = tmp_path / "base.json"
    base_path.write_text(json.dumps(base), encoding="utf-8")
    resolved_category = _categorical_review()
    model = ScriptedStructuredModel(
        {
            "natural_criterion_ai_audit": lambda payload: {
                "reviews": [resolved_category]
            }
        }
    )

    result = run_natural_evaluation_max_resolution(
        source_path=source,
        base_review_path=base_path,
        review_output_path=tmp_path / "resolved.json",
        gold_output_path=tmp_path / "resolved-gold.json",
        checkpoint_dir=tmp_path / "max-checkpoints",
        model=model,
        model_id="scripted-local",
        effort="max",
        concurrency=1,
    )

    assert result["maximum_review_line_count"] == 1
    assert result["criterion_count"] == 2
    assert result["remaining_uncertain_count"] == 0
    assert model.call_count["natural_criterion_ai_audit"] == 1


def test_failed_resolution_preserves_the_base_review(tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"trials": [_trial()]}), encoding="utf-8")
    category = _categorical_review()
    category["confidence"] = "medium"
    base_rows = [_numeric_review(), _heading_review("exclude"), category]
    rows = []
    for review in base_rows:
        candidate = next(
            item
            for item in _trial()["criterion_candidates"]
            if item["candidate_id"] == review["candidate_id"]
        )
        rows.append(
            {
                "group_id": "type_2_diabetes",
                "nct_id": "NCT90000001",
                "candidate_id": review["candidate_id"],
                "section": candidate["section_hint"],
                "line_number": candidate["line_number"],
                "start_char": candidate["start_char"],
                "end_char": candidate["end_char"],
                "source_text": candidate["source_text"],
                **{key: value for key, value in review.items() if key != "candidate_id"},
            }
        )
    base_path = tmp_path / "base.json"
    base_path.write_text(
        json.dumps({"model": "base", "effort": "medium", "reviews": rows}),
        encoding="utf-8",
    )
    model = ScriptedStructuredModel(
        {"natural_criterion_ai_audit": lambda _: {"reviews": []}}
    )

    result = run_natural_evaluation_max_resolution(
        source_path=source,
        base_review_path=base_path,
        review_output_path=tmp_path / "review.json",
        gold_output_path=tmp_path / "gold.json",
        checkpoint_dir=tmp_path / "checkpoints",
        model=model,
        model_id="scripted-local",
        effort="max",
        concurrency=1,
    )

    assert result["criterion_count"] == 2
    assert result["usage"]["failed_model_calls"] == 1


def test_conservative_gold_keeps_high_confidence_and_reports_low_coverage(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"trials": [_trial()]}), encoding="utf-8")
    rows = []
    reviews = [_numeric_review(), _heading_review("exclude"), _categorical_review()]
    reviews[2]["annotations"].append(
        {
            "fact_code": "current_glp_1_therapy",
            "fact_description": "Current GLP-1 therapy",
            "criterion_summary": "Currently uses GLP-1 therapy",
            "expected_value": "present",
            "operator": None,
            "threshold": None,
            "unit": None,
        }
    )
    for review in reviews:
        candidate = next(
            item
            for item in _trial()["criterion_candidates"]
            if item["candidate_id"] == review["candidate_id"]
        )
        rows.append(
            {
                "group_id": "type_2_diabetes",
                "nct_id": "NCT90000001",
                "candidate_id": review["candidate_id"],
                "section": candidate["section_hint"],
                "line_number": candidate["line_number"],
                "start_char": candidate["start_char"],
                "end_char": candidate["end_char"],
                "source_text": candidate["source_text"],
                **{key: value for key, value in review.items() if key != "candidate_id"},
            }
        )
    review_path = tmp_path / "tiered.json"
    review_path.write_text(
        json.dumps(
            {
                "source_sha256": hashlib.sha256(
                    source.read_bytes()
                ).hexdigest(),
                "reviews": rows,
            }
        ),
        encoding="utf-8",
    )
    config = tmp_path / "selection.json"
    config.write_text(
        json.dumps(
            {
                "protocol_id": "synthetic-test",
                "selection_seed": "frozen-test",
                "source": "ClinicalTrials.gov API v2",
                "page_size": 10,
                "sort": "LastUpdatePostDate:desc",
                "allowed_overall_statuses": ["RECRUITING"],
                "allowed_study_types": ["INTERVENTIONAL"],
                "minimum_objective_lines": 2,
                "maximum_objective_lines": 10,
                "groups": [
                    {
                        "group_id": "type_2_diabetes",
                        "label": "Type 2 diabetes",
                        "query_condition": "type 2 diabetes",
                        "accepted_condition_terms": ["type 2 diabetes"],
                        "target_count": 1,
                        "reserve_count": 0,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = build_conservative_natural_ai_gold(
        source_path=source,
        tiered_review_path=review_path,
        selection_config_path=config,
        output_path=tmp_path / "conservative.json",
    )

    assert result["criterion_count"] == 1
    assert result["accepted_source_line_count"] == 1
    assert result["high_confidence_annotation_count"] == 3
    assert result["deferred_complex_source_line_count"] == 1
    assert result["low_coverage_trial_ids"] == ["NCT90000001"]
    output = json.loads(
        (tmp_path / "conservative.json").read_text(encoding="utf-8")
    )
    assert output["criteria"][0]["fact_code"] == "age"
    assert output["authority"].startswith("High-confidence AI research draft")


def test_maximum_resolution_can_recheck_every_included_line(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"trials": [_trial()]}), encoding="utf-8")
    base_reviews = [_numeric_review(), _heading_review("exclude"), _categorical_review()]
    rows = []
    for review in base_reviews:
        candidate = next(
            item
            for item in _trial()["criterion_candidates"]
            if item["candidate_id"] == review["candidate_id"]
        )
        rows.append(
            {
                "group_id": "type_2_diabetes",
                "nct_id": "NCT90000001",
                "candidate_id": review["candidate_id"],
                "section": candidate["section_hint"],
                "line_number": candidate["line_number"],
                "start_char": candidate["start_char"],
                "end_char": candidate["end_char"],
                "source_text": candidate["source_text"],
                **{key: value for key, value in review.items() if key != "candidate_id"},
            }
        )
    base_path = tmp_path / "base.json"
    base_path.write_text(
        json.dumps({"model": "base", "effort": "medium", "reviews": rows}),
        encoding="utf-8",
    )
    model = ScriptedStructuredModel(
        {
            "natural_criterion_ai_audit": lambda payload: {
                "reviews": [
                    item
                    for item in base_reviews
                    if item["candidate_id"]
                    in {row["candidate_id"] for row in payload["source_lines"]}
                ]
            }
        }
    )

    result = run_natural_evaluation_max_resolution(
        source_path=source,
        base_review_path=base_path,
        review_output_path=tmp_path / "review.json",
        gold_output_path=tmp_path / "gold.json",
        checkpoint_dir=tmp_path / "checkpoints",
        model=model,
        model_id="scripted-local",
        effort="max",
        concurrency=1,
        chunk_size=1,
        selection_mode="included",
    )

    assert result["maximum_review_line_count"] == 2
    assert model.call_count["natural_criterion_ai_audit"] == 2


def test_committed_conservative_ai_gold_is_source_bound_and_representable() -> None:
    root = Path(__file__).parents[1]
    source_path = root / "data" / "natural_evaluation_v1" / "criterion_review.json"
    gold_path = (
        root
        / "data"
        / "natural_evaluation_v1"
        / "ai_preliminary_gold_conservative.json"
    )
    source = json.loads(source_path.read_text(encoding="utf-8"))
    gold = json.loads(gold_path.read_text(encoding="utf-8"))

    assert gold["source_sha256"] == hashlib.sha256(
        source_path.read_bytes()
    ).hexdigest()
    assert gold["criterion_count"] >= 60
    criteria = gold["criteria"]
    assert len({row["criterion_id"] for row in criteria}) == len(criteria)
    source_by_id = {
        item["candidate_id"]: item
        for trial in source["trials"]
        for item in trial["criterion_candidates"]
    }
    grouped: dict[str, list[dict]] = {}
    for row in criteria:
        source_row = source_by_id[row["candidate_id"]]
        assert row["source_text"] == source_row["source_text"]
        assert row["line_number"] == source_row["line_number"]
        grouped.setdefault(row["candidate_id"], []).append(row)
    for rows in grouped.values():
        if len(rows) == 1:
            continue
        assert all(row["operator"] is not None for row in rows)
        assert len({(row["fact_code"], row["unit"]) for row in rows}) == 1
        operators = {row["operator"] for row in rows}
        assert operators & {"gt", "gte"}
        assert operators & {"lt", "lte"}


def test_resolution_cli_passes_included_selection_mode(monkeypatch) -> None:
    captured = {}

    class FakePool:
        def __init__(self, **kwargs) -> None:
            pass

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback) -> None:
            pass

    def fake_resolution(**kwargs):
        captured.update(kwargs)
        return {
            "review_output": "review.json",
            "gold_output": "gold.json",
            "source_line_count": 3,
            "maximum_review_line_count": 2,
            "criterion_count": 1,
            "remaining_uncertain_count": 0,
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "thinking_tokens": 1,
                "total_tokens": 3,
            },
        }

    monkeypatch.setattr(cli_module, "CodexSubscriptionModelPool", FakePool)
    monkeypatch.setattr(
        cli_module,
        "run_natural_evaluation_max_resolution",
        fake_resolution,
    )

    exit_code = cli_module.main(
        [
            "resolve-natural-evaluation-ai-review",
            "--selection-mode",
            "included",
            "--confirm-subscription-run",
        ]
    )

    assert exit_code == 0
    assert captured["selection_mode"] == "included"
