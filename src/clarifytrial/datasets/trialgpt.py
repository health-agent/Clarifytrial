"""TrialGPT Criterion Annotations download, validation, and pilot sampling."""

from __future__ import annotations

import json
import random
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


DATASET_ID = "ncbi/TrialGPT-Criterion-Annotations"
DATASET_PAGE = f"https://huggingface.co/datasets/{DATASET_ID}"
ROWS_ENDPOINT = "https://datasets-server.huggingface.co/rows"
EXPECTED_ROW_COUNT = 1_015
PILOT_SEED = 20_260_820

CriterionType = Literal["inclusion", "exclusion"]
EligibilityLabel = Literal[
    "included",
    "not included",
    "excluded",
    "not excluded",
    "not enough information",
    "not applicable",
]
PairCategory = Literal[
    "clear",
    "unresolved_only",
    "violation_and_unresolved",
    "violation_only",
]


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class TrialGPTCriterionRow(_StrictModel):
    """One public patient-criterion annotation row."""

    annotation_id: int = Field(ge=0)
    patient_id: str = Field(min_length=1)
    note: str = Field(min_length=1)
    trial_id: str = Field(min_length=1)
    trial_title: str = Field(min_length=1)
    criterion_type: CriterionType
    criterion_text: str | None = Field(default=None, min_length=1)
    gpt4_explanation: str
    explanation_correctness: str
    gpt4_sentences: list[int]
    expert_sentences: list[int]
    gpt4_eligibility: EligibilityLabel
    expert_eligibility: EligibilityLabel
    training: bool

    @field_validator("gpt4_sentences", "expert_sentences", mode="before")
    @classmethod
    def sentence_id_strings_are_decoded(cls, value: Any) -> Any:
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("sentence identifiers are not valid JSON") from exc
        return value

    @field_validator("gpt4_sentences", "expert_sentences")
    @classmethod
    def sentence_ids_are_unique_non_negative(cls, value: list[int]) -> list[int]:
        if any(item < 0 for item in value):
            raise ValueError("sentence identifiers must be non-negative")
        if len(value) != len(set(value)):
            raise ValueError("sentence identifiers must be unique")
        return value

    @model_validator(mode="after")
    def eligibility_labels_match_criterion_type(self) -> "TrialGPTCriterionRow":
        allowed = (
            {"included", "not included", "not enough information", "not applicable"}
            if self.criterion_type == "inclusion"
            else {"excluded", "not excluded", "not enough information", "not applicable"}
        )
        if self.gpt4_eligibility not in allowed:
            raise ValueError("gpt4 eligibility label does not match criterion type")
        if self.expert_eligibility not in allowed:
            raise ValueError("expert eligibility label does not match criterion type")
        return self


class TrialGPTTrialMetadata(_StrictModel):
    """Trial context used by the public TrialGPT matching prompt."""

    trial_id: str = Field(min_length=1)
    brief_title: str = Field(min_length=1)
    diseases_list: list[str] = Field(default_factory=list)
    drugs_list: list[str] = Field(default_factory=list)
    brief_summary: str = ""


class TrialGPTPair(_StrictModel):
    """All annotated criteria for one patient-trial pair."""

    patient_id: str = Field(min_length=1)
    trial_id: str = Field(min_length=1)
    note: str = Field(min_length=1)
    trial_title: str = Field(min_length=1)
    criteria: list[TrialGPTCriterionRow] = Field(min_length=1)
    category: PairCategory
    metadata: TrialGPTTrialMetadata | None = None


@dataclass(frozen=True, slots=True)
class TrialGPTPatientSplit:
    """Development pairs and the two non-development patient partitions."""

    development_pairs: tuple[TrialGPTPair, ...]
    held_out_pairs: tuple[TrialGPTPair, ...]
    overlap_patient_pairs: tuple[TrialGPTPair, ...]


JsonFetcher = Callable[[str], Mapping[str, Any]]


def _fetch_json(url: str, timeout_seconds: float = 60) -> Mapping[str, Any]:
    request = urllib.request.Request(
        url,
        headers={"accept": "application/json", "user-agent": "ClarifyTrial/0.1"},
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        value = json.loads(response.read().decode("utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("dataset server did not return a JSON object")
    return value


def _rows_url(offset: int, length: int) -> str:
    query = urllib.parse.urlencode(
        {
            "dataset": DATASET_ID,
            "config": "default",
            "split": "train",
            "offset": offset,
            "length": length,
        }
    )
    return f"{ROWS_ENDPOINT}?{query}"


def fetch_trialgpt_dataset(
    cache_dir: str | Path,
    *,
    force: bool = False,
    fetch_json: JsonFetcher | None = None,
    expected_total: int = EXPECTED_ROW_COUNT,
    page_size: int = 100,
) -> tuple[Path, Path]:
    """Download the public rows to an ignored cache and retain source metadata."""

    destination = Path(cache_dir)
    raw_path = destination / "criterion_annotations.jsonl"
    metadata_path = destination / "source_metadata.json"
    if raw_path.is_file() and metadata_path.is_file() and not force:
        rows = load_trialgpt_rows(raw_path)
        if len(rows) != expected_total:
            raise ValueError(
                f"cached TrialGPT row count is {len(rows)}, expected {expected_total}"
            )
        return raw_path, metadata_path

    getter = fetch_json or _fetch_json
    collected: list[TrialGPTCriterionRow] = []
    reported_total: int | None = None
    for offset in range(0, expected_total, page_size):
        length = min(page_size, expected_total - offset)
        payload = getter(_rows_url(offset, length))
        page_total = payload.get("num_rows_total")
        if not isinstance(page_total, int):
            raise ValueError("dataset server omitted num_rows_total")
        if reported_total is None:
            reported_total = page_total
        elif reported_total != page_total:
            raise ValueError("dataset row count changed during download")
        raw_rows = payload.get("rows")
        if not isinstance(raw_rows, list):
            raise ValueError("dataset server omitted rows")
        for item in raw_rows:
            if not isinstance(item, Mapping) or not isinstance(item.get("row"), Mapping):
                raise ValueError("dataset server returned an invalid row envelope")
            collected.append(TrialGPTCriterionRow.model_validate(item["row"]))

    if reported_total != expected_total or len(collected) != expected_total:
        raise ValueError(
            "TrialGPT row count does not match the pinned pilot expectation: "
            f"reported={reported_total}, received={len(collected)}, "
            f"expected={expected_total}"
        )
    annotation_ids = [row.annotation_id for row in collected]
    if len(annotation_ids) != len(set(annotation_ids)):
        raise ValueError("TrialGPT annotation_id values are not unique")

    collected.sort(key=lambda row: row.annotation_id)
    destination.mkdir(parents=True, exist_ok=True)
    with raw_path.open("w", encoding="utf-8", newline="\n") as stream:
        for row in collected:
            stream.write(row.model_dump_json())
            stream.write("\n")

    metadata = {
        "dataset_id": DATASET_ID,
        "dataset_page": DATASET_PAGE,
        "rows_endpoint": ROWS_ENDPOINT,
        "split": "train",
        "license": "public-domain",
        "retrieved_at": datetime.now(timezone.utc).isoformat(),
        "row_count": len(collected),
        "statistics": summarize_trialgpt_rows(collected),
    }
    with metadata_path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(metadata, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    return raw_path, metadata_path


def load_trialgpt_rows(path: str | Path) -> list[TrialGPTCriterionRow]:
    """Read validated annotation rows from the local cache."""

    rows: list[TrialGPTCriterionRow] = []
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                rows.append(TrialGPTCriterionRow.model_validate_json(line))
            except ValueError as exc:
                raise ValueError(f"invalid TrialGPT row at line {line_number}") from exc
    annotation_ids = [row.annotation_id for row in rows]
    if len(annotation_ids) != len(set(annotation_ids)):
        raise ValueError("TrialGPT annotation_id values are not unique")
    return rows


def load_sigir_trial_metadata(
    path: str | Path,
) -> dict[str, TrialGPTTrialMetadata]:
    """Read the SIGIR trial corpus distributed with the TrialGPT repository."""

    trials: dict[str, TrialGPTTrialMetadata] = {}
    with Path(path).open(encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                trial_id = item["_id"]
                metadata = item["metadata"]
                parsed = TrialGPTTrialMetadata(
                    trial_id=trial_id,
                    brief_title=metadata.get("brief_title") or item.get("title") or trial_id,
                    diseases_list=list(metadata.get("diseases_list") or []),
                    drugs_list=list(metadata.get("drugs_list") or []),
                    brief_summary=metadata.get("brief_summary") or "",
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"invalid SIGIR corpus row at line {line_number}") from exc
            if parsed.trial_id in trials:
                raise ValueError(f"duplicate SIGIR trial ID: {parsed.trial_id}")
            trials[parsed.trial_id] = parsed
    return trials


def _pair_category(rows: Sequence[TrialGPTCriterionRow]) -> PairCategory:
    unresolved = any(
        row.expert_eligibility == "not enough information" for row in rows
    )
    violation = any(
        (row.criterion_type == "inclusion" and row.expert_eligibility == "not included")
        or (row.criterion_type == "exclusion" and row.expert_eligibility == "excluded")
        for row in rows
    )
    if unresolved and violation:
        return "violation_and_unresolved"
    if unresolved:
        return "unresolved_only"
    if violation:
        return "violation_only"
    return "clear"


def group_patient_trial_pairs(
    rows: Iterable[TrialGPTCriterionRow],
    metadata: Mapping[str, TrialGPTTrialMetadata] | None = None,
) -> list[TrialGPTPair]:
    """Group criterion labels into the two-call unit used by TrialGPT matching."""

    grouped: dict[tuple[str, str], list[TrialGPTCriterionRow]] = defaultdict(list)
    for row in rows:
        grouped[(row.patient_id, row.trial_id)].append(row)

    pairs: list[TrialGPTPair] = []
    for key in sorted(grouped):
        group = sorted(grouped[key], key=lambda row: row.annotation_id)
        patient_id, trial_id = key
        if len({row.note for row in group}) != 1:
            raise ValueError(f"patient note differs inside pair {patient_id}/{trial_id}")
        if len({row.trial_title for row in group}) != 1:
            raise ValueError(f"trial title differs inside pair {patient_id}/{trial_id}")
        pairs.append(
            TrialGPTPair(
                patient_id=patient_id,
                trial_id=trial_id,
                note=group[0].note,
                trial_title=group[0].trial_title,
                criteria=group,
                category=_pair_category(group),
                metadata=None if metadata is None else metadata.get(trial_id),
            )
        )
    return pairs


def _pair_id(pair: TrialGPTPair) -> tuple[str, str]:
    return pair.patient_id, pair.trial_id


def _require_unique_pair_ids(pairs: Sequence[TrialGPTPair]) -> None:
    pair_ids = [_pair_id(pair) for pair in pairs]
    if len(pair_ids) != len(set(pair_ids)):
        raise ValueError("patient-trial pair identifiers must be unique")


def select_full_trialgpt_pairs(
    pairs: Sequence[TrialGPTPair],
) -> list[TrialGPTPair]:
    """Return every fully specified pair in stable patient/trial order.

    A pair is fully specified only when every annotated criterion has its public
    criterion text.  Incomplete pairs remain in the source rows but cannot be
    sent through the criterion-judgment pilot.
    """

    _require_unique_pair_ids(pairs)
    return sorted(
        (
            pair
            for pair in pairs
            if all(row.criterion_text is not None for row in pair.criteria)
        ),
        key=_pair_id,
    )


def split_trialgpt_pairs_by_patient(
    pairs: Sequence[TrialGPTPair],
    *,
    seed: int = PILOT_SEED,
) -> TrialGPTPatientSplit:
    """Keep the existing 20-pair pilot as development data without patient leak.

    All remaining complete pairs from patients unseen in development form the
    held-out partition.  Other trials for a development patient are retained in
    a separate overlap partition instead of being silently discarded or scored
    as independent evaluation data.
    """

    full_pairs = select_full_trialgpt_pairs(pairs)
    development = select_pilot_pairs(full_pairs, seed=seed)
    development_ids = {_pair_id(pair) for pair in development}
    development_patients = {pair.patient_id for pair in development}

    held_out: list[TrialGPTPair] = []
    overlap: list[TrialGPTPair] = []
    for pair in full_pairs:
        if _pair_id(pair) in development_ids:
            continue
        if pair.patient_id in development_patients:
            overlap.append(pair)
        else:
            held_out.append(pair)

    held_out_patients = {pair.patient_id for pair in held_out}
    if development_patients & held_out_patients:
        raise AssertionError("development patients leaked into held-out pairs")

    partition_ids = {
        *development_ids,
        *(_pair_id(pair) for pair in held_out),
        *(_pair_id(pair) for pair in overlap),
    }
    full_ids = {_pair_id(pair) for pair in full_pairs}
    if partition_ids != full_ids:
        raise AssertionError("patient split did not preserve every complete pair")

    return TrialGPTPatientSplit(
        development_pairs=tuple(development),
        held_out_pairs=tuple(held_out),
        overlap_patient_pairs=tuple(overlap),
    )


def _choose_near_positions(
    candidates: Sequence[TrialGPTPair],
    count: int,
    used_patients: set[str],
    rng: random.Random,
) -> list[TrialGPTPair]:
    if len(candidates) < count:
        raise ValueError(f"pilot stratum has {len(candidates)} pairs, needs {count}")
    decorated = [(len(pair.criteria), rng.random(), pair) for pair in candidates]
    decorated.sort(key=lambda item: (item[0], item[1], item[2].patient_id, item[2].trial_id))
    chosen: list[TrialGPTPair] = []
    used_keys: set[tuple[str, str]] = set()
    if count == 1:
        desired_positions = [(len(decorated) - 1) / 2]
    else:
        desired_positions = [
            index * (len(decorated) - 1) / (count - 1) for index in range(count)
        ]
    for desired in desired_positions:
        ranked = sorted(
            enumerate(decorated),
            key=lambda item: (
                item[1][2].patient_id in used_patients,
                abs(item[0] - desired),
                item[1][1],
            ),
        )
        for _, (_, _, pair) in ranked:
            key = (pair.patient_id, pair.trial_id)
            if key in used_keys:
                continue
            chosen.append(pair)
            used_keys.add(key)
            used_patients.add(pair.patient_id)
            break
    return chosen


def select_pilot_pairs(
    pairs: Sequence[TrialGPTPair],
    *,
    size: int = 20,
    seed: int = PILOT_SEED,
) -> list[TrialGPTPair]:
    """Choose a reproducible, state-stratified 20-pair cost pilot."""

    if size != 20:
        raise ValueError("the pinned TrialGPT pilot currently contains exactly 20 pairs")
    targets: dict[PairCategory, int] = {
        "clear": 2,
        "unresolved_only": 13,
        "violation_and_unresolved": 4,
        "violation_only": 1,
    }
    by_category: dict[PairCategory, list[TrialGPTPair]] = defaultdict(list)
    for pair in pairs:
        if any(row.criterion_text is None for row in pair.criteria):
            continue
        by_category[pair.category].append(pair)

    rng = random.Random(seed)
    used_patients: set[str] = set()
    selected: list[TrialGPTPair] = []
    for category in (
        "violation_only",
        "clear",
        "violation_and_unresolved",
        "unresolved_only",
    ):
        selected.extend(
            _choose_near_positions(
                by_category[category],
                targets[category],
                used_patients,
                rng,
            )
        )
    if len(selected) != size:
        raise AssertionError("pilot sampling did not produce the requested size")
    return selected


def summarize_trialgpt_rows(
    rows: Sequence[TrialGPTCriterionRow],
) -> dict[str, Any]:
    """Return auditable counts without storing patient text in the summary."""

    pairs = {(row.patient_id, row.trial_id) for row in rows}
    gpt_nei_expert_decisive = sum(
        row.gpt4_eligibility == "not enough information"
        and row.expert_eligibility != "not enough information"
        for row in rows
    )
    return {
        "criterion_rows": len(rows),
        "patients": len({row.patient_id for row in rows}),
        "patient_trial_pairs": len(pairs),
        "trials": len({row.trial_id for row in rows}),
        "rows_missing_criterion_text": sum(
            row.criterion_text is None for row in rows
        ),
        "expert_label_counts": dict(
            sorted(Counter(row.expert_eligibility for row in rows).items())
        ),
        "trialgpt_label_counts": dict(
            sorted(Counter(row.gpt4_eligibility for row in rows).items())
        ),
        "trialgpt_nei_expert_decisive": gpt_nei_expert_decisive,
    }
