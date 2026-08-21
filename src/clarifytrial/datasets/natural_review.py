"""Compare two independent reviews without deciding disagreements in code."""

from __future__ import annotations

import csv
import json
import math
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any


_SOURCE_FIELDS = (
    "group_id",
    "nct_id",
    "title",
    "candidate_id",
    "section_hint",
    "line_number",
    "start_char",
    "end_char",
    "source_text",
    "detection_reasons",
)
_ANNOTATION_FIELDS = (
    "include_in_objective_gold",
    "kind",
    "fact_code",
    "operator",
    "threshold",
    "unit",
    "max_age_days",
    "allowed_source_types",
    "allowed_verification_statuses",
)
_ALLOWED_KINDS = {"inclusion", "exclusion"}
_ALLOWED_OPERATORS = {"gt", "gte", "lt", "lte", "eq"}
_ALLOWED_SOURCE_TYPES = {
    "medical_record",
    "patient_report",
    "official_verification",
}
_ALLOWED_VERIFICATION_STATUSES = {
    "verified",
    "reported",
    "pending",
    "conflicting",
}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _base_rows(review_path: str | Path) -> list[dict[str, str]]:
    payload = json.loads(Path(review_path).read_text(encoding="utf-8"))
    rows = []
    for trial in payload.get("trials", []):
        for candidate in trial.get("criterion_candidates", []):
            rows.append(
                {
                    "group_id": str(trial["group_id"]),
                    "nct_id": str(trial["nct_id"]),
                    "title": str(trial["title"]),
                    "candidate_id": str(candidate["candidate_id"]),
                    "section_hint": str(candidate["section_hint"]),
                    "line_number": str(candidate["line_number"]),
                    "start_char": str(candidate["start_char"]),
                    "end_char": str(candidate["end_char"]),
                    "source_text": str(candidate["source_text"]),
                    "detection_reasons": ";".join(candidate["detection_reasons"]),
                }
            )
    if not rows:
        raise ValueError("review source has no criterion candidates")
    return rows


def _load_sheet(path: str | Path, reviewer_id: str) -> list[dict[str, str]]:
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [dict(item) for item in csv.DictReader(handle)]
    if not rows:
        raise ValueError(f"review sheet is empty: {path}")
    if any(item.get("reviewer_id", "").strip() != reviewer_id for item in rows):
        raise ValueError(f"reviewer_id differs in {path}")
    keys = []
    for item in rows:
        candidate_id = item.get("candidate_id", "").strip()
        raw_index = item.get("annotation_index", "").strip()
        try:
            annotation_index = int(raw_index)
        except ValueError as exc:
            raise ValueError(f"annotation_index must be an integer in {path}") from exc
        if annotation_index < 1:
            raise ValueError(f"annotation_index must be positive in {path}")
        keys.append((candidate_id, annotation_index))
    if len(keys) != len(set(keys)):
        raise ValueError(f"candidate_id and annotation_index are repeated in {path}")
    return rows


def _boolean(value: str) -> bool | None:
    normalized = value.strip().casefold()
    if not normalized:
        return None
    if normalized in {"true", "yes", "1", "y"}:
        return True
    if normalized in {"false", "no", "0", "n"}:
        return False
    raise ValueError(f"invalid include_in_objective_gold value: {value!r}")


def _number(value: str, *, integer: bool = False) -> int | float | None:
    normalized = value.strip()
    if not normalized:
        return None
    parsed = float(normalized)
    if not math.isfinite(parsed):
        raise ValueError(f"numeric value must be finite: {value!r}")
    if integer:
        if not parsed.is_integer() or parsed < 0:
            raise ValueError(f"max_age_days must be a nonnegative integer: {value!r}")
        return int(parsed)
    return parsed


def _values(value: str) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                item.strip().casefold()
                for item in re.split(r"[;,]", value)
                if item.strip()
            }
        )
    )


def _annotation(row: Mapping[str, str]) -> dict[str, Any] | None:
    include = _boolean(row.get("include_in_objective_gold", ""))
    if include is None:
        return None
    if include is False:
        return {"include_in_objective_gold": False}

    kind = row.get("kind", "").strip().casefold()
    fact_code = row.get("fact_code", "").strip().casefold()
    operator = row.get("operator", "").strip().casefold()
    threshold = _number(row.get("threshold", ""))
    unit = row.get("unit", "").strip()
    max_age_days = _number(row.get("max_age_days", ""), integer=True)
    source_types = _values(row.get("allowed_source_types", ""))
    verification_statuses = _values(
        row.get("allowed_verification_statuses", "")
    )
    if kind not in _ALLOWED_KINDS:
        raise ValueError(f"invalid criterion kind for {row.get('candidate_id')}")
    if not fact_code:
        raise ValueError(f"fact_code is missing for {row.get('candidate_id')}")
    if not re.fullmatch(r"[a-z0-9_]+", fact_code):
        raise ValueError("fact_code must use letters, numbers, and underscores")
    numeric_fields = (bool(operator), threshold is not None, bool(unit))
    if any(numeric_fields) and not all(numeric_fields):
        raise ValueError(
            f"operator, threshold, and unit must be filled together for "
            f"{row.get('candidate_id')}"
        )
    if operator and operator not in _ALLOWED_OPERATORS:
        raise ValueError(f"invalid operator for {row.get('candidate_id')}")
    if not set(source_types) <= _ALLOWED_SOURCE_TYPES:
        raise ValueError(f"invalid source type for {row.get('candidate_id')}")
    if not set(verification_statuses) <= _ALLOWED_VERIFICATION_STATUSES:
        raise ValueError(f"invalid verification status for {row.get('candidate_id')}")
    return {
        "include_in_objective_gold": True,
        "kind": kind,
        "fact_code": fact_code,
        "operator": operator or None,
        "threshold": threshold,
        "unit": unit or None,
        "max_age_days": max_age_days,
        "allowed_source_types": list(source_types),
        "allowed_verification_statuses": list(verification_statuses),
    }


def compare_natural_evaluation_reviews(
    review_source_path: str | Path,
    reviewer_1_path: str | Path,
    reviewer_2_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Separate agreements, disagreements, and unfinished rows."""

    base_rows = _base_rows(review_source_path)
    reviewer_1 = _load_sheet(reviewer_1_path, "reviewer_1")
    reviewer_2 = _load_sheet(reviewer_2_path, "reviewer_2")
    base_ids = [item["candidate_id"] for item in base_rows]
    rows_1 = {
        (item["candidate_id"].strip(), int(item["annotation_index"])): item
        for item in reviewer_1
    }
    rows_2 = {
        (item["candidate_id"].strip(), int(item["annotation_index"])): item
        for item in reviewer_2
    }
    if {item[0] for item in rows_1} != set(base_ids) or {
        item[0] for item in rows_2
    } != set(base_ids):
        raise ValueError("review sheets must contain every source candidate ID")

    agreements = []
    disagreements = []
    incomplete = []
    for base in base_rows:
        candidate_id = base["candidate_id"]
        indices = sorted(
            {key[1] for key in rows_1 if key[0] == candidate_id}
            | {key[1] for key in rows_2 if key[0] == candidate_id}
        )
        for annotation_index in indices:
            first = rows_1.get((candidate_id, annotation_index))
            second = rows_2.get((candidate_id, annotation_index))
            if first is None or second is None:
                incomplete.append(
                    {
                        **base,
                        "annotation_index": annotation_index,
                        "reviewer_1_complete": first is not None,
                        "reviewer_2_complete": second is not None,
                    }
                )
                continue
            for field in _SOURCE_FIELDS:
                if first.get(field, "").strip() != base[field]:
                    raise ValueError(
                        f"reviewer_1 changed source field {field}: {candidate_id}"
                    )
                if second.get(field, "").strip() != base[field]:
                    raise ValueError(
                        f"reviewer_2 changed source field {field}: {candidate_id}"
                    )
            annotation_1 = _annotation(first)
            annotation_2 = _annotation(second)
            if annotation_1 is None or annotation_2 is None:
                incomplete.append(
                    {
                        **base,
                        "annotation_index": annotation_index,
                        "reviewer_1_complete": annotation_1 is not None,
                        "reviewer_2_complete": annotation_2 is not None,
                    }
                )
                continue
            if annotation_1 == annotation_2:
                agreements.append(
                    {
                        **base,
                        "annotation_index": annotation_index,
                        "annotation": annotation_1,
                    }
                )
                continue
            differing_fields = [
                field
                for field in _ANNOTATION_FIELDS
                if annotation_1.get(field) != annotation_2.get(field)
            ]
            disagreements.append(
                {
                    **base,
                    "annotation_index": annotation_index,
                    "differing_fields": differing_fields,
                    "reviewer_1": annotation_1,
                    "reviewer_2": annotation_2,
                }
            )

    result = {
        "status": (
            "incomplete"
            if incomplete
            else "needs_resolution"
            if disagreements
            else "all_agreed"
        ),
        "source_candidate_count": len(base_rows),
        "compared_annotation_count": len(agreements)
        + len(disagreements)
        + len(incomplete),
        "agreement_count": len(agreements),
        "disagreement_count": len(disagreements),
        "incomplete_count": len(incomplete),
        "agreements": agreements,
        "disagreements": disagreements,
        "incomplete": incomplete,
    }
    _write_json(Path(output_path), result)
    return result
