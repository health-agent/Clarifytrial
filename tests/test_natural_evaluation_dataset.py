from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import pytest

from clarifytrial.datasets import (
    CLARIFYTRIAL_V5_NCT_IDS,
    audit_natural_evaluation_review,
    compare_natural_evaluation_reviews,
    objective_criterion_candidates,
    prepare_natural_evaluation_sources,
)


ELIGIBILITY = """Inclusion Criteria:
* HbA1c must be at least 7.0%.
* Age must be between 18 and 70 years.
* BMI must be below 40 kg/m2.
* Able to understand instructions.
Exclusion Criteria:
* Current use of insulin.
"""

NUMBERED_ELIGIBILITY = """3.1 Inclusion Criteria
* Age must be at least 18 years.
7.0% HbA1c is required.
3.2 Exclusion Criteria
* Current use of insulin.
"""


def _study(
    nct_id: str,
    *,
    condition: str = "Type 2 Diabetes Mellitus",
    study_type: str = "INTERVENTIONAL",
    status: str = "RECRUITING",
    eligibility: str = ELIGIBILITY,
) -> dict[str, Any]:
    return {
        "protocolSection": {
            "identificationModule": {
                "nctId": nct_id,
                "briefTitle": f"Synthetic public study {nct_id}",
            },
            "statusModule": {"overallStatus": status},
            "designModule": {"studyType": study_type},
            "conditionsModule": {"conditions": [condition]},
            "eligibilityModule": {"eligibilityCriteria": eligibility},
        }
    }


def _write_config(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "protocol_id": "test-natural-evaluation",
                "selection_seed": "fixed-before-results",
                "source": "ClinicalTrials.gov API v2",
                "page_size": 100,
                "sort": "LastUpdatePostDate:desc",
                "allowed_overall_statuses": ["RECRUITING"],
                "allowed_study_types": ["INTERVENTIONAL"],
                "minimum_objective_lines": 4,
                "maximum_objective_lines": 8,
                "groups": [
                    {
                        "group_id": "type_2_diabetes",
                        "label": "제2형 당뇨병",
                        "query_condition": "type 2 diabetes",
                        "accepted_condition_terms": ["type 2 diabetes"],
                        "target_count": 2,
                        "reserve_count": 1,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _complete_sheet(path: Path, *, first_operator: str = "gte") -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    for row in rows:
        row["include_in_objective_gold"] = "false"
    rows[0].update(
        {
            "include_in_objective_gold": "true",
            "kind": "inclusion",
            "fact_code": "hba1c",
            "operator": first_operator,
            "threshold": "7.0",
            "unit": "%",
            "max_age_days": "14",
            "allowed_source_types": "official_verification",
            "allowed_verification_statuses": "verified",
        }
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _add_second_annotation(path: Path) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    second = dict(rows[0])
    second.update(
        {
            "annotation_index": "2",
            "fact_code": "age",
            "operator": "gte",
            "threshold": "18",
            "unit": "years",
            "max_age_days": "",
            "allowed_source_types": "medical_record",
            "allowed_verification_statuses": "verified",
        }
    )
    rows.insert(1, second)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _include_categorical_annotation(path: Path) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = list(reader.fieldnames or [])
        rows = list(reader)
    row = next(item for item in rows if "understand instructions" in item["source_text"])
    row.update(
        {
            "include_in_objective_gold": "true",
            "kind": "inclusion",
            "fact_code": "understands_instructions",
            "operator": "",
            "threshold": "",
            "unit": "",
        }
    )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def test_objective_candidates_keep_source_spans_and_sections() -> None:
    rows = objective_criterion_candidates(ELIGIBILITY)

    assert len(rows) == 4
    assert [item["section_hint"] for item in rows] == [
        "inclusion",
        "inclusion",
        "inclusion",
        "exclusion",
    ]
    for item in rows:
        assert ELIGIBILITY[item["start_char"] : item["end_char"]] == item[
            "source_text"
        ]


def test_numbered_section_headings_are_not_treated_as_criteria() -> None:
    rows = objective_criterion_candidates(NUMBERED_ELIGIBILITY)

    assert [item["section_hint"] for item in rows] == [
        "inclusion",
        "inclusion",
        "exclusion",
    ]
    assert all("Criteria" not in item["source_text"] for item in rows)
    assert rows[1]["display_text"] == "7.0% HbA1c is required."


def test_selection_is_deterministic_and_excludes_invalid_studies(
    tmp_path: Path,
) -> None:
    config = tmp_path / "selection.json"
    _write_config(config)
    existing_id = CLARIFYTRIAL_V5_NCT_IDS["type_2_diabetes"][0]
    valid = [_study(f"NCT9000000{index}") for index in range(6)]
    search_rows = [
        *valid,
        _study(existing_id),
        _study("NCT91000001", condition="Type 1 Diabetes"),
        _study(
            "NCT91000004",
            condition="Overweight Without Type 2 Diabetes",
        ),
        _study("NCT91000002", study_type="OBSERVATIONAL"),
        _study(
            "NCT91000003",
            eligibility="Inclusion Criteria:\n* Age must be at least 18 years.\n",
        ),
    ]

    def fetch_json(url: str):
        if url.endswith("/version"):
            return {"apiVersion": "2.0", "dataTimestamp": "test-timestamp"}
        return {"studies": list(reversed(search_rows)), "totalCount": len(search_rows)}

    first_review = tmp_path / "first" / "review.json"
    first = prepare_natural_evaluation_sources(
        config,
        tmp_path / "first-cache",
        first_review,
        force=True,
        fetch_json=fetch_json,
    )
    second_review = tmp_path / "second" / "review.json"
    second = prepare_natural_evaluation_sources(
        config,
        tmp_path / "second-cache",
        second_review,
        force=True,
        fetch_json=lambda url: (
            {"apiVersion": "2.0", "dataTimestamp": "test-timestamp"}
            if url.endswith("/version")
            else {"studies": search_rows, "totalCount": len(search_rows)}
        ),
    )

    assert first["primary_study_count"] == 2
    assert first["reserve_study_count"] == 1
    assert first["primary_objective_candidate_count"] == 8
    assert first["primary_review_candidate_count"] == 10
    assert first["audit"]["passed"] is True
    assert first["audit"]["review_sheet_row_counts"] == {
        "reviewer_1": 10,
        "reviewer_2": 10,
    }
    first_payload = json.loads(first_review.read_text(encoding="utf-8"))
    second_payload = json.loads(second_review.read_text(encoding="utf-8"))
    first_ids = [item["nct_id"] for item in first_payload["trials"]]
    second_ids = [item["nct_id"] for item in second_payload["trials"]]
    reserve_ids = [item["nct_id"] for item in first_payload["reserve_trials"]]
    assert first_ids == second_ids
    assert not set(first_ids) & set(reserve_ids)
    assert existing_id not in first_ids + reserve_ids
    assert "NCT91000001" not in first_ids + reserve_ids
    assert "NCT91000002" not in first_ids + reserve_ids
    assert "NCT91000003" not in first_ids + reserve_ids
    assert "NCT91000004" not in first_ids + reserve_ids

    for trial in first_payload["trials"]:
        record = json.loads(
            (
                tmp_path
                / "first-cache"
                / "records"
                / f"{trial['nct_id']}.json"
            ).read_text(encoding="utf-8")
        )
        source = record["protocolSection"]["eligibilityModule"][
            "eligibilityCriteria"
        ]
        for candidate in trial["criterion_candidates"]:
            assert (
                source[candidate["start_char"] : candidate["end_char"]]
                == candidate["source_text"]
            )
            assert candidate["review"]["resolution"] == "pending"

    audited = audit_natural_evaluation_review(
        first_review,
        tmp_path / "first-cache",
        config,
    )
    assert audited["source_span_count"] == 10
    with (tmp_path / "first" / "reviewer_1.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        rows = list(csv.DictReader(handle))
    assert len(rows) == 10
    assert {item["reviewer_id"] for item in rows} == {"reviewer_1"}
    assert all(item["include_in_objective_gold"] == "" for item in rows)

    rows[0]["reviewer_notes"] = "human work must be preserved"
    with (tmp_path / "first" / "reviewer_1.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    reused = prepare_natural_evaluation_sources(
        config,
        tmp_path / "first-cache",
        first_review,
        fetch_json=lambda _: (_ for _ in ()).throw(
            AssertionError("frozen cache reuse must not call the API")
        ),
    )
    assert reused["audit"]["passed"] is True
    with (tmp_path / "first" / "reviewer_1.csv").open(
        "r", encoding="utf-8-sig", newline=""
    ) as handle:
        preserved = list(csv.DictReader(handle))
    assert preserved[0]["reviewer_notes"] == "human work must be preserved"
    with pytest.raises(ValueError, match="source refresh would invalidate"):
        prepare_natural_evaluation_sources(
            config,
            tmp_path / "first-cache",
            first_review,
            force=True,
            fetch_json=fetch_json,
        )

    reviewer_1 = tmp_path / "first" / "reviewer_1.csv"
    reviewer_2 = tmp_path / "first" / "reviewer_2.csv"
    _complete_sheet(reviewer_1)
    _complete_sheet(reviewer_2)
    _include_categorical_annotation(reviewer_1)
    _include_categorical_annotation(reviewer_2)
    comparison = compare_natural_evaluation_reviews(
        first_review,
        reviewer_1,
        reviewer_2,
        tmp_path / "first" / "comparison.json",
    )
    assert comparison["status"] == "all_agreed"
    assert comparison["agreement_count"] == 10

    _complete_sheet(reviewer_2, first_operator="lt")
    _include_categorical_annotation(reviewer_2)
    disagreement = compare_natural_evaluation_reviews(
        first_review,
        reviewer_1,
        reviewer_2,
        tmp_path / "first" / "disagreement.json",
    )
    assert disagreement["status"] == "needs_resolution"
    assert disagreement["disagreement_count"] == 1
    assert disagreement["disagreements"][0]["differing_fields"] == ["operator"]

    _complete_sheet(reviewer_1)
    _complete_sheet(reviewer_2)
    _add_second_annotation(reviewer_1)
    _add_second_annotation(reviewer_2)
    multiple = compare_natural_evaluation_reviews(
        first_review,
        reviewer_1,
        reviewer_2,
        tmp_path / "first" / "multiple.json",
    )
    assert multiple["status"] == "all_agreed"
    assert multiple["source_candidate_count"] == 10
    assert multiple["compared_annotation_count"] == 11
    reused_after_duplicate = prepare_natural_evaluation_sources(
        config,
        tmp_path / "first-cache",
        first_review,
        fetch_json=lambda _: (_ for _ in ()).throw(
            AssertionError("frozen cache reuse must not call the API")
        ),
    )
    assert reused_after_duplicate["audit"]["passed"] is True
