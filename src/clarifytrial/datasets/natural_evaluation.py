"""Prepare frozen ClinicalTrials.gov sources for natural-input evaluation."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .clinicaltrials_gov import (
    API_ROOT,
    CLARIFYTRIAL_V5_NCT_IDS,
    TERMS_URL,
)


JsonFetcher = Callable[[str], Mapping[str, Any]]


class _ConfigModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NaturalEvaluationGroupConfig(_ConfigModel):
    """One disease-specific ClinicalTrials.gov selection frame."""

    group_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    query_condition: str = Field(min_length=1)
    accepted_condition_terms: list[str] = Field(min_length=1)
    target_count: int = Field(default=5, ge=1)
    reserve_count: int = Field(default=5, ge=0)


class NaturalEvaluationSelectionConfig(_ConfigModel):
    """Public, reviewable rules used before any model evaluation result exists."""

    protocol_id: str = Field(min_length=1)
    selection_seed: str = Field(min_length=1)
    source: Literal["ClinicalTrials.gov API v2"]
    page_size: int = Field(default=100, ge=1, le=1000)
    sort: str = Field(default="LastUpdatePostDate:desc", min_length=1)
    allowed_overall_statuses: list[str] = Field(min_length=1)
    allowed_study_types: list[str] = Field(
        default_factory=lambda: ["INTERVENTIONAL"],
        min_length=1,
    )
    minimum_objective_lines: int = Field(default=4, ge=1)
    maximum_objective_lines: int = Field(default=25, ge=1)
    groups: list[NaturalEvaluationGroupConfig] = Field(min_length=1)

    @model_validator(mode="after")
    def ranges_and_groups_are_valid(self) -> "NaturalEvaluationSelectionConfig":
        if self.maximum_objective_lines < self.minimum_objective_lines:
            raise ValueError("maximum_objective_lines must not be smaller than minimum")
        group_ids = [item.group_id for item in self.groups]
        if len(group_ids) != len(set(group_ids)):
            raise ValueError("groups must not repeat group_id")
        return self


def load_natural_evaluation_selection_config(
    path: str | Path,
) -> NaturalEvaluationSelectionConfig:
    """Load the frozen selection rules."""

    return NaturalEvaluationSelectionConfig.model_validate_json(
        Path(path).read_text(encoding="utf-8")
    )


def _fetch_json(url: str) -> Mapping[str, Any]:
    request = Request(url, headers={"User-Agent": "ClarifyTrial-research/0.1"})
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_reviewer_csv(
    review: Mapping[str, Any],
    path: Path,
    reviewer_id: str,
) -> None:
    fieldnames = [
        "reviewer_id",
        "group_id",
        "nct_id",
        "title",
        "candidate_id",
        "annotation_index",
        "section_hint",
        "line_number",
        "start_char",
        "end_char",
        "source_text",
        "detection_reasons",
        "include_in_objective_gold",
        "kind",
        "fact_code",
        "operator",
        "threshold",
        "unit",
        "max_age_days",
        "allowed_source_types",
        "allowed_verification_statuses",
        "reviewer_notes",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for trial in review["trials"]:
            for candidate in trial["criterion_candidates"]:
                writer.writerow(
                    {
                        "reviewer_id": reviewer_id,
                        "group_id": trial["group_id"],
                        "nct_id": trial["nct_id"],
                        "title": trial["title"],
                        "candidate_id": candidate["candidate_id"],
                        "annotation_index": 1,
                        "section_hint": candidate["section_hint"],
                        "line_number": candidate["line_number"],
                        "start_char": candidate["start_char"],
                        "end_char": candidate["end_char"],
                        "source_text": candidate["source_text"],
                        "detection_reasons": ";".join(
                            candidate["detection_reasons"]
                        ),
                        "include_in_objective_gold": "",
                        "kind": "",
                        "fact_code": "",
                        "operator": "",
                        "threshold": "",
                        "unit": "",
                        "max_age_days": "",
                        "allowed_source_types": "",
                        "allowed_verification_statuses": "",
                        "reviewer_notes": "",
                    }
                )


def _normalized_label(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


_SECTION_PATTERN = re.compile(
    r"^(?:\d+(?:\.\d+)*[.)]?\s*)?(?:key\s+)?"
    r"(inclusion|exclusion)\s+criteria\b.*$",
    re.IGNORECASE,
)
_BULLET_PATTERN = re.compile(r"^(?:(?:[*•-])\s*|(?:\d+[.)])\s+)")
_NUMBER_PATTERN = re.compile(r"(?<![a-z])\d+(?:[.,]\d+)?", re.IGNORECASE)
_COMPARISON_PATTERN = re.compile(
    r"(?:[<>≤≥]=?|\bat\s+(?:least|most)\b|\bbetween\b|\bfrom\b.+\bto\b|"
    r"\bmore\s+than\b|\bless\s+than\b|\bno\s+(?:more|less)\s+than\b)",
    re.IGNORECASE,
)
_DURATION_PATTERN = re.compile(
    r"\b(?:day|days|week|weeks|month|months|year|years|hour|hours)\b",
    re.IGNORECASE,
)
_MEASUREMENT_PATTERN = re.compile(
    r"\b(?:age|bmi|body\s+mass\s+index|hba1c|a1c|ecog|madrs|score|"
    r"laborator(?:y|ies)|platelet|neutrophil|hemoglobin|creatinine|egfr|"
    r"bilirubin|ast|alt|inr|lvef|blood\s+pressure|weight)\b",
    re.IGNORECASE,
)
_STATUS_PATTERN = re.compile(
    r"\b(?:positive|negative|active|current\s+use|no\s+prior|without|"
    r"history\s+of|confirmed|documented|stable|failure\s+of)\b",
    re.IGNORECASE,
)


def _detection_reasons(text: str) -> list[str]:
    reasons = []
    for label, pattern in (
        ("number", _NUMBER_PATTERN),
        ("comparison", _COMPARISON_PATTERN),
        ("duration", _DURATION_PATTERN),
        ("measurement", _MEASUREMENT_PATTERN),
        ("explicit_status", _STATUS_PATTERN),
    ):
        if pattern.search(text):
            reasons.append(label)
    return reasons


def _criterion_lines(eligibility_text: str) -> list[dict[str, Any]]:
    """Return every non-heading source line with its section and exact span."""

    candidates = []
    section: Literal["inclusion", "exclusion", "unknown"] = "unknown"
    position = 0
    for line_number, line_with_ending in enumerate(
        eligibility_text.splitlines(keepends=True),
        start=1,
    ):
        raw_line = line_with_ending.rstrip("\r\n")
        stripped = raw_line.strip()
        leading = len(raw_line) - len(raw_line.lstrip())
        start_char = position + leading
        end_char = start_char + len(stripped)
        position += len(line_with_ending)
        if not stripped:
            continue
        heading = _SECTION_PATTERN.match(stripped)
        if heading:
            section = (
                "inclusion"
                if heading.group(1).casefold() == "inclusion"
                else "exclusion"
            )
            continue
        display_text = _BULLET_PATTERN.sub("", stripped).strip()
        reasons = _detection_reasons(display_text)
        candidates.append(
            {
                "line_number": line_number,
                "source_text": stripped,
                "display_text": display_text,
                "start_char": start_char,
                "end_char": end_char,
                "section_hint": section,
                "detection_reasons": reasons,
            }
        )
    return candidates


def objective_criterion_candidates(eligibility_text: str) -> list[dict[str, Any]]:
    """Return source-exact lines that may support objective annotation.

    These hints are used only to select a manageable trial set. Human reviewers
    receive every criterion line so a missed hint cannot disappear from gold.
    """

    return [
        item
        for item in _criterion_lines(eligibility_text)
        if item["detection_reasons"]
    ]


def _protocol_section(study: Mapping[str, Any]) -> Mapping[str, Any]:
    protocol = study.get("protocolSection")
    if not isinstance(protocol, Mapping):
        raise ValueError("study has no protocolSection")
    return protocol


def _eligibility_text(study: Mapping[str, Any]) -> str:
    protocol = _protocol_section(study)
    eligibility_module = protocol.get("eligibilityModule")
    if not isinstance(eligibility_module, Mapping):
        raise ValueError("study has no eligibilityModule")
    text = eligibility_module.get("eligibilityCriteria")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("study has no eligibility criteria")
    return text


def _nct_id(study: Mapping[str, Any]) -> str:
    identification = _protocol_section(study).get("identificationModule")
    if not isinstance(identification, Mapping):
        raise ValueError("study has no identificationModule")
    value = identification.get("nctId")
    if not isinstance(value, str) or not value:
        raise ValueError("study has no NCT ID")
    return value


def _study_title(study: Mapping[str, Any]) -> str:
    identification = _protocol_section(study).get("identificationModule")
    if not isinstance(identification, Mapping):
        raise ValueError("study has no identificationModule")
    for key in ("briefTitle", "officialTitle"):
        value = identification.get(key)
        if isinstance(value, str) and value:
            return value
    raise ValueError("study has no title")


def _overall_status(study: Mapping[str, Any]) -> str:
    status = _protocol_section(study).get("statusModule")
    if not isinstance(status, Mapping) or not isinstance(
        status.get("overallStatus"), str
    ):
        raise ValueError("study has no overall status")
    return str(status["overallStatus"])


def _study_type(study: Mapping[str, Any]) -> str:
    design = _protocol_section(study).get("designModule")
    if not isinstance(design, Mapping) or not isinstance(
        design.get("studyType"), str
    ):
        raise ValueError("study has no study type")
    return str(design["studyType"])


def _condition_names(study: Mapping[str, Any]) -> list[str]:
    module = _protocol_section(study).get("conditionsModule")
    if not isinstance(module, Mapping):
        return []
    values = module.get("conditions")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        return []
    return [str(item) for item in values if isinstance(item, str) and item]


def _condition_is_in_scope(
    study: Mapping[str, Any],
    accepted_terms: Sequence[str],
) -> bool:
    normalized_terms = [_normalized_label(item) for item in accepted_terms]
    for condition in _condition_names(study):
        normalized_condition = _normalized_label(condition)
        for term in normalized_terms:
            position = normalized_condition.find(term)
            if position < 0:
                continue
            prefix = normalized_condition[max(0, position - 24) : position]
            if re.search(
                r"\b(?:without|excluding|except|non|no|not)\s*$",
                prefix,
            ):
                continue
            return True
    return False


def _selection_key(seed: str, group_id: str, nct_id: str) -> str:
    return hashlib.sha256(f"{seed}|{group_id}|{nct_id}".encode()).hexdigest()


def _query_url(
    config: NaturalEvaluationSelectionConfig,
    group: NaturalEvaluationGroupConfig,
) -> str:
    parameters = {
        "query.cond": group.query_condition,
        "filter.overallStatus": "|".join(config.allowed_overall_statuses),
        "pageSize": str(config.page_size),
        "format": "json",
        "countTotal": "true",
        "sort": config.sort,
    }
    return f"{API_ROOT}/studies?{urlencode(parameters, safe='|:')}"


def _study_row(
    study: Mapping[str, Any],
    *,
    group_id: str,
    seed: str,
) -> dict[str, Any]:
    eligibility = _eligibility_text(study)
    review_candidates = _criterion_lines(eligibility)
    objective_candidates = [
        item for item in review_candidates if item["detection_reasons"]
    ]
    nct_id = _nct_id(study)
    return {
        "group_id": group_id,
        "nct_id": nct_id,
        "title": _study_title(study),
        "overall_status": _overall_status(study),
        "study_type": _study_type(study),
        "conditions": _condition_names(study),
        "objective_candidate_count": len(objective_candidates),
        "review_candidate_count": len(review_candidates),
        "eligibility_sha256": hashlib.sha256(eligibility.encode()).hexdigest(),
        "selection_key": _selection_key(seed, group_id, nct_id),
        "study_url": f"https://clinicaltrials.gov/study/{nct_id}",
        "api_url": f"{API_ROOT}/studies/{nct_id}",
        "review_candidates": review_candidates,
    }


def _review_trial(row: Mapping[str, Any], selection_role: str) -> dict[str, Any]:
    criteria = []
    for position, candidate in enumerate(row["review_candidates"], start=1):
        criteria.append(
            {
                "candidate_id": f"{row['nct_id']}:candidate:{position:03d}",
                **candidate,
                "review": {
                    "include_in_objective_gold": None,
                    "kind": None,
                    "fact_code": None,
                    "operator": None,
                    "threshold": None,
                    "unit": None,
                    "max_age_days": None,
                    "allowed_source_types": [],
                    "allowed_verification_statuses": [],
                    "reviewer_1": None,
                    "reviewer_2": None,
                    "resolution": "pending",
                },
            }
        )
    return {
        key: row[key]
        for key in (
            "group_id",
            "nct_id",
            "title",
            "overall_status",
            "conditions",
            "objective_candidate_count",
            "review_candidate_count",
            "eligibility_sha256",
            "study_url",
        )
    } | {"selection_role": selection_role, "criterion_candidates": criteria}


def prepare_natural_evaluation_sources(
    selection_config_path: str | Path,
    cache_dir: str | Path,
    review_output_path: str | Path,
    *,
    force: bool = False,
    overwrite_review: bool = False,
    fetch_json: JsonFetcher | None = None,
) -> dict[str, Any]:
    """Select, freeze, and prepare public trials for two-person review."""

    config = load_natural_evaluation_selection_config(selection_config_path)
    getter = fetch_json or _fetch_json
    destination = Path(cache_dir)
    search_dir = destination / "search_results"
    records_dir = destination / "records"
    metadata_path = destination / "source_metadata.json"
    review_path = Path(review_output_path)
    reviewer_1_path = review_path.with_name("reviewer_1.csv")
    reviewer_2_path = review_path.with_name("reviewer_2.csv")
    review_paths = (review_path, reviewer_1_path, reviewer_2_path)
    existing_review_count = sum(item.exists() for item in review_paths)
    if existing_review_count not in {0, len(review_paths)}:
        raise ValueError("the review JSON and both reviewer sheets must exist together")
    if force and existing_review_count and not overwrite_review:
        raise ValueError(
            "source refresh would invalidate existing review sheets; "
            "use overwrite_review only before human review begins"
        )
    search_paths = [search_dir / f"{item.group_id}.json" for item in config.groups]
    reuse_frozen_cache = (
        not force
        and metadata_path.exists()
        and all(item.exists() for item in search_paths)
    )
    if existing_review_count and not overwrite_review and not reuse_frozen_cache:
        raise ValueError(
            "existing review sheets require their original complete source cache"
        )
    if reuse_frozen_cache:
        frozen_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        version: Mapping[str, Any] = {
            "apiVersion": frozen_metadata.get("api_version"),
            "dataTimestamp": frozen_metadata.get("data_timestamp"),
        }
        retrieved_at = str(frozen_metadata.get("retrieved_at"))
    else:
        version = getter(f"{API_ROOT}/version")
        retrieved_at = datetime.now(timezone.utc).isoformat()
    excluded_ids = {
        nct_id
        for nct_ids in CLARIFYTRIAL_V5_NCT_IDS.values()
        for nct_id in nct_ids
    }
    selected_rows = []
    group_summaries = []
    records_by_id: dict[str, Mapping[str, Any]] = {}

    for group in config.groups:
        search_path = search_dir / f"{group.group_id}.json"
        if not reuse_frozen_cache:
            search_response = getter(_query_url(config, group))
            _write_json(search_path, search_response)
        else:
            search_response = json.loads(search_path.read_text(encoding="utf-8"))
        studies = search_response.get("studies")
        if not isinstance(studies, Sequence) or isinstance(studies, (str, bytes)):
            raise ValueError(f"search response has no studies for {group.group_id}")
        eligible_rows = []
        for study in studies:
            if not isinstance(study, Mapping):
                continue
            try:
                nct_id = _nct_id(study)
                if nct_id in excluded_ids:
                    continue
                if _overall_status(study) not in config.allowed_overall_statuses:
                    continue
                if _study_type(study) not in config.allowed_study_types:
                    continue
                if not _condition_is_in_scope(study, group.accepted_condition_terms):
                    continue
                row = _study_row(
                    study,
                    group_id=group.group_id,
                    seed=config.selection_seed,
                )
            except ValueError:
                continue
            if not (
                config.minimum_objective_lines
                <= row["objective_candidate_count"]
                <= config.maximum_objective_lines
            ):
                continue
            eligible_rows.append(row)
            records_by_id[nct_id] = study

        eligible_rows.sort(key=lambda item: (item["selection_key"], item["nct_id"]))
        required_count = group.target_count + group.reserve_count
        if len(eligible_rows) < required_count:
            raise ValueError(
                f"{group.group_id} has {len(eligible_rows)} eligible trials; "
                f"{required_count} are required"
            )
        chosen = eligible_rows[:required_count]
        for position, row in enumerate(chosen):
            selection_role = (
                "primary" if position < group.target_count else "reserve"
            )
            selected_rows.append({**row, "selection_role": selection_role})
            record_path = records_dir / f"{row['nct_id']}.json"
            _write_json(record_path, records_by_id[row["nct_id"]])
        group_summaries.append(
            {
                "group_id": group.group_id,
                "search_result_count": len(studies),
                "search_total_count": search_response.get("totalCount"),
                "has_more_search_results": bool(
                    search_response.get("nextPageToken")
                ),
                "eligible_count": len(eligible_rows),
                "primary_count": group.target_count,
                "reserve_count": group.reserve_count,
                "primary_objective_candidate_count": sum(
                    item["objective_candidate_count"]
                    for item in chosen[: group.target_count]
                ),
                "primary_review_candidate_count": sum(
                    item["review_candidate_count"]
                    for item in chosen[: group.target_count]
                ),
            }
        )

    metadata = {
        "protocol_id": config.protocol_id,
        "source": config.source,
        "api_version": version.get("apiVersion"),
        "data_timestamp": version.get("dataTimestamp"),
        "retrieved_at": retrieved_at,
        "terms_url": TERMS_URL,
        "attribution": "ClinicalTrials.gov, U.S. National Library of Medicine",
        "selection_config": str(selection_config_path),
        "selection_seed": config.selection_seed,
        "excluded_development_study_count": len(excluded_ids),
        "groups": group_summaries,
        "selected_studies": [
            {key: row[key] for key in row if key != "review_candidates"}
            for row in selected_rows
        ],
    }
    _write_json(metadata_path, metadata)

    review = {
        "protocol_id": config.protocol_id,
        "status": "draft_unreviewed",
        "source": config.source,
        "data_timestamp": version.get("dataTimestamp"),
        "retrieved_at": retrieved_at,
        "attribution": metadata["attribution"],
        "selection_summary": group_summaries,
        "instructions": (
            "자동 탐지는 검토 후보일 뿐 정답이 아니다. 두 사람이 공식 원문과 각 "
            "필드를 독립적으로 확인하고 합의한 항목만 고정 평가 정답으로 옮긴다."
        ),
        "trials": [
            _review_trial(row, "primary")
            for row in selected_rows
            if row["selection_role"] == "primary"
        ],
        "reserve_trials": [
            {
                key: row[key]
                for key in (
                    "group_id",
                    "nct_id",
                    "title",
                    "overall_status",
                    "conditions",
                    "objective_candidate_count",
                    "review_candidate_count",
                    "eligibility_sha256",
                    "study_url",
                )
            }
            for row in selected_rows
            if row["selection_role"] == "reserve"
        ],
    }
    if existing_review_count and not overwrite_review:
        existing_review = json.loads(review_path.read_text(encoding="utf-8"))
        expected_primary_ids = [item["nct_id"] for item in review["trials"]]
        expected_reserve_ids = [item["nct_id"] for item in review["reserve_trials"]]
        if [item["nct_id"] for item in existing_review.get("trials", [])] != (
            expected_primary_ids
        ) or [
            item["nct_id"] for item in existing_review.get("reserve_trials", [])
        ] != expected_reserve_ids:
            raise ValueError("existing review sheets differ from frozen selection")
        review = existing_review
    else:
        _write_json(review_path, review)
        _write_reviewer_csv(review, reviewer_1_path, "reviewer_1")
        _write_reviewer_csv(review, reviewer_2_path, "reviewer_2")
    audit = audit_natural_evaluation_review(
        review_output_path,
        cache_dir,
        selection_config_path,
    )
    return {
        "metadata_path": str(metadata_path),
        "review_output_path": str(review_output_path),
        "reviewer_1_path": str(reviewer_1_path),
        "reviewer_2_path": str(reviewer_2_path),
        "primary_study_count": len(review["trials"]),
        "reserve_study_count": len(review["reserve_trials"]),
        "primary_objective_candidate_count": sum(
            item["objective_candidate_count"] for item in review["trials"]
        ),
        "primary_review_candidate_count": sum(
            len(item["criterion_candidates"]) for item in review["trials"]
        ),
        "group_summaries": group_summaries,
        "audit": audit,
    }


def audit_natural_evaluation_review(
    review_path: str | Path,
    cache_dir: str | Path,
    selection_config_path: str | Path,
) -> dict[str, Any]:
    """Verify that a review draft is closed over its frozen public sources."""

    review = json.loads(Path(review_path).read_text(encoding="utf-8"))
    config = load_natural_evaluation_selection_config(selection_config_path)
    if review.get("protocol_id") != config.protocol_id:
        raise ValueError("review protocol_id differs from selection config")
    primary = review.get("trials")
    reserves = review.get("reserve_trials")
    if not isinstance(primary, list) or not isinstance(reserves, list):
        raise ValueError("review must contain trials and reserve_trials lists")

    primary_ids = [item.get("nct_id") for item in primary]
    reserve_ids = [item.get("nct_id") for item in reserves]
    all_ids = primary_ids + reserve_ids
    if any(not isinstance(item, str) or not item for item in all_ids):
        raise ValueError("every review trial needs an NCT ID")
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("primary and reserve trials must not overlap")
    development_ids = {
        nct_id
        for nct_ids in CLARIFYTRIAL_V5_NCT_IDS.values()
        for nct_id in nct_ids
    }
    overlap = set(all_ids) & development_ids
    if overlap:
        raise ValueError(
            "review trials overlap development studies: " + ", ".join(sorted(overlap))
        )

    expected_primary = {item.group_id: item.target_count for item in config.groups}
    expected_reserve = {item.group_id: item.reserve_count for item in config.groups}
    primary_by_group: dict[str, int] = {}
    reserve_by_group: dict[str, int] = {}
    for item in primary:
        group_id = item.get("group_id")
        primary_by_group[group_id] = primary_by_group.get(group_id, 0) + 1
    for item in reserves:
        group_id = item.get("group_id")
        reserve_by_group[group_id] = reserve_by_group.get(group_id, 0) + 1
    if primary_by_group != expected_primary:
        raise ValueError("primary trial counts differ from the selection config")
    if reserve_by_group != expected_reserve:
        raise ValueError("reserve trial counts differ from the selection config")

    records_dir = Path(cache_dir) / "records"
    candidate_ids: list[str] = []
    candidate_sources: dict[str, dict[str, str]] = {}
    candidate_count_by_group: dict[str, int] = {}
    source_span_count = 0
    for trial in [*primary, *reserves]:
        nct_id = trial["nct_id"]
        record_path = records_dir / f"{nct_id}.json"
        if not record_path.exists():
            raise ValueError(f"source record is missing for {nct_id}")
        record = json.loads(record_path.read_text(encoding="utf-8"))
        if _nct_id(record) != nct_id:
            raise ValueError(f"source record ID mismatch for {nct_id}")
        source = _eligibility_text(record)
        digest = hashlib.sha256(source.encode()).hexdigest()
        if digest != trial.get("eligibility_sha256"):
            raise ValueError(f"eligibility source hash mismatch for {nct_id}")

        if nct_id in reserve_ids:
            continue
        candidates = trial.get("criterion_candidates")
        if not isinstance(candidates, list):
            raise ValueError(f"criterion_candidates are missing for {nct_id}")
        if len(candidates) != trial.get("review_candidate_count"):
            raise ValueError(f"candidate count mismatch for {nct_id}")
        candidate_count_by_group[trial["group_id"]] = (
            candidate_count_by_group.get(trial["group_id"], 0) + len(candidates)
        )
        for candidate in candidates:
            candidate_id = candidate.get("candidate_id")
            if not isinstance(candidate_id, str) or not candidate_id:
                raise ValueError(f"candidate ID is missing for {nct_id}")
            candidate_ids.append(candidate_id)
            candidate_sources[candidate_id] = {
                "group_id": str(trial["group_id"]),
                "nct_id": str(nct_id),
                "title": str(trial["title"]),
                "candidate_id": candidate_id,
                "section_hint": str(candidate.get("section_hint", "")),
                "line_number": str(candidate.get("line_number", "")),
                "start_char": str(candidate.get("start_char", "")),
                "end_char": str(candidate.get("end_char", "")),
                "source_text": str(candidate.get("source_text", "")),
                "detection_reasons": ";".join(
                    str(item) for item in candidate.get("detection_reasons", [])
                ),
            }
            start = candidate.get("start_char")
            end = candidate.get("end_char")
            source_text = candidate.get("source_text")
            if not isinstance(start, int) or not isinstance(end, int):
                raise ValueError(f"source span is missing for {candidate_id}")
            if source[start:end] != source_text:
                raise ValueError(f"source span mismatch for {candidate_id}")
            source_span_count += 1
            if candidate.get("section_hint") not in {"inclusion", "exclusion"}:
                raise ValueError(f"criterion section is unresolved for {candidate_id}")
            annotation = candidate.get("review")
            if not isinstance(annotation, Mapping):
                raise ValueError(f"review fields are missing for {candidate_id}")
            if annotation.get("resolution") not in {
                "pending",
                "agreed",
                "uncertain",
                "excluded",
            }:
                raise ValueError(f"invalid review resolution for {candidate_id}")

    if len(candidate_ids) != len(set(candidate_ids)):
        raise ValueError("criterion candidate IDs must be unique")
    minimum_source_spans = sum(
        item.target_count * config.minimum_objective_lines for item in config.groups
    )
    if source_span_count < minimum_source_spans:
        raise ValueError(
            "primary review draft has fewer source candidates than the frozen minimum"
        )
    sheet_row_counts = {}
    for reviewer_id in ("reviewer_1", "reviewer_2"):
        sheet_path = Path(review_path).with_name(f"{reviewer_id}.csv")
        if not sheet_path.exists():
            raise ValueError(f"review sheet is missing: {sheet_path}")
        with sheet_path.open("r", encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        keys: list[tuple[str, int]] = []
        for row in rows:
            candidate_id = str(row.get("candidate_id", "")).strip()
            try:
                annotation_index = int(str(row.get("annotation_index", "")))
            except ValueError as exc:
                raise ValueError(
                    f"annotation index is not an integer: {reviewer_id}"
                ) from exc
            if annotation_index < 1:
                raise ValueError(f"annotation index is not positive: {reviewer_id}")
            keys.append((candidate_id, annotation_index))
            expected = candidate_sources.get(candidate_id)
            if expected is None:
                raise ValueError(f"unknown candidate in sheet: {candidate_id}")
            for field, expected_value in expected.items():
                if str(row.get(field, "")).strip() != expected_value:
                    raise ValueError(
                        f"review sheet changed source field {field}: {candidate_id}"
                    )
        if len(keys) != len(set(keys)):
            raise ValueError(f"review sheet repeats an annotation: {reviewer_id}")
        if {item[0] for item in keys} != set(candidate_ids):
            raise ValueError(f"review sheet candidate set differs: {reviewer_id}")
        if any(item.get("reviewer_id") != reviewer_id for item in rows):
            raise ValueError(f"reviewer ID differs in sheet: {reviewer_id}")
        sheet_row_counts[reviewer_id] = len(rows)
    return {
        "passed": True,
        "primary_study_count": len(primary),
        "reserve_study_count": len(reserves),
        "source_span_count": source_span_count,
        "candidate_count_by_group": candidate_count_by_group,
        "development_overlap_count": 0,
        "unknown_section_count": 0,
        "review_sheet_row_counts": sheet_row_counts,
    }
