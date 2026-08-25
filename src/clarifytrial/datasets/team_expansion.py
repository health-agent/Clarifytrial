"""Select a broad, inspectable 50-trial pool from the team snapshot."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from pydantic import Field, model_validator

from ..contracts import ContractModel
from ..io import atomic_write_text
from ..preparation.team_trials import (
    DEFAULT_ENROLLING_STATUSES,
    TEAM_TRIALS_URL,
    TeamTrialRecord,
    inspect_team_trial_corpus,
    iter_team_trial_records,
)


class ExpansionDiseaseGroup(ContractModel):
    group_id: str = Field(min_length=1)
    group_label: str = Field(min_length=1)
    condition_aliases: list[str] = Field(min_length=1)
    target_count: int = Field(default=5, ge=1)


class ExpansionSelectionConfig(ContractModel):
    protocol_id: str = Field(min_length=1)
    disease_groups: list[ExpansionDiseaseGroup] = Field(min_length=1)
    excluded_trial_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def group_ids_are_unique(self) -> "ExpansionSelectionConfig":
        values = [item.group_id for item in self.disease_groups]
        if len(values) != len(set(values)):
            raise ValueError("disease groups must not repeat group_id")
        if len(self.excluded_trial_ids) != len(set(self.excluded_trial_ids)):
            raise ValueError("excluded trial IDs must not repeat")
        return self


_CATEGORY_PATTERNS: dict[str, tuple[str, ...]] = {
    "numeric_threshold": (
        r"\b(?:at least|at most|greater than|less than|between)\b",
        r"[<>≤≥]",
        r"\b\d+(?:\.\d+)?\s*(?:mg|kg|cm|mm|%|years?)\b",
    ),
    "time_window": (
        r"\bwithin\s+\d+",
        r"\b(?:days?|weeks?|months?|years?)\s+(?:before|after|prior)",
        r"\bprior\s+to\b",
    ),
    "medication_or_treatment": (
        r"\bmedication\b",
        r"\bdrug\b",
        r"\btherapy\b",
        r"\btreatment\b",
        r"\bchemotherapy\b",
    ),
    "surgery_or_pathology": (
        r"\bsurger(?:y|ies)\b",
        r"\bpatholog(?:y|ical)\b",
        r"\bhistolog(?:y|ical)\b",
        r"\bbiopsy\b",
    ),
    "pregnancy_or_contraception": (
        r"\bpregnan",
        r"\bcontracept",
        r"\bchildbearing\b",
    ),
    "patient_answerable": (
        r"\bwilling\b",
        r"\bable to\b",
        r"\bconsent\b",
        r"\bsmok(?:e|er|ing)\b",
        r"\bself.report",
    ),
    "laboratory_or_imaging": (
        r"\blaboratory\b",
        r"\bblood\b",
        r"\bimaging\b",
        r"\bscan\b",
        r"\bmri\b",
        r"\bct\b",
    ),
}


def _criterion_categories(text: str) -> tuple[str, ...]:
    normalized = text.casefold()
    return tuple(
        category
        for category, patterns in _CATEGORY_PATTERNS.items()
        if any(re.search(pattern, normalized) for pattern in patterns)
    )


def _matches_group(record: TeamTrialRecord, group: ExpansionDiseaseGroup) -> bool:
    conditions = " | ".join(record.conditions).casefold()
    return any(alias.casefold() in conditions for alias in group.condition_aliases)


def _select_group_trials(
    candidates: list[TeamTrialRecord],
    *,
    target_count: int,
    already_selected: set[str],
) -> list[tuple[TeamTrialRecord, tuple[str, ...]]]:
    remaining = [item for item in candidates if item.nct_id not in already_selected]
    selected: list[tuple[TeamTrialRecord, tuple[str, ...]]] = []
    covered: set[str] = set()
    while remaining and len(selected) < target_count:
        decorated = []
        for item in remaining:
            categories = _criterion_categories(item.eligibility_text)
            decorated.append(
                (
                    len(set(categories) - covered),
                    len(categories),
                    min(len(item.eligibility_text), 12_000),
                    item.nct_id,
                    item,
                    categories,
                )
            )
        # Prefer new condition types, then richer source text.  NCT ID keeps
        # the final tie deterministic and independent of file order.
        best = max(decorated, key=lambda item: (item[0], item[1], item[2], item[3]))
        record, categories = best[4], best[5]
        selected.append((record, categories))
        covered.update(categories)
        remaining = [item for item in remaining if item.nct_id != record.nct_id]
    return selected


def select_team_evaluation_trials(
    *,
    corpus_path: str | Path,
    config_path: str | Path,
    destination: str | Path,
) -> dict:
    """Create the reproducible curation pool used before detailed labeling."""

    config = ExpansionSelectionConfig.model_validate_json(
        Path(config_path).read_text(encoding="utf-8")
    )
    excluded_trial_ids = set(config.excluded_trial_ids)
    records = [
        item
        for item in iter_team_trial_records(corpus_path)
        if item.overall_status.upper() in DEFAULT_ENROLLING_STATUSES
        and item.nct_id not in excluded_trial_ids
    ]
    corpus_summary = inspect_team_trial_corpus(corpus_path)
    used: set[str] = set()
    groups = []
    all_selected = []
    category_counts: Counter[str] = Counter()
    for group in config.disease_groups:
        candidates = [item for item in records if _matches_group(item, group)]
        selected = _select_group_trials(
            candidates,
            target_count=group.target_count,
            already_selected=used,
        )
        if len(selected) != group.target_count:
            raise ValueError(
                f"group {group.group_id!r} has only {len(selected)} unique trials; "
                f"needs {group.target_count}"
            )
        rows = []
        for record, categories in selected:
            used.add(record.nct_id)
            category_counts.update(categories)
            row = {
                "nct_id": record.nct_id,
                "title": record.title,
                "conditions": record.conditions,
                "overall_status": record.overall_status,
                "criterion_categories": list(categories),
                "eligibility_text_length": len(record.eligibility_text),
                "source_location": f"{TEAM_TRIALS_URL}#nct_id={record.nct_id}",
            }
            rows.append(row)
            all_selected.append({"group_id": group.group_id, **row})
        groups.append(
            {
                "group_id": group.group_id,
                "group_label": group.group_label,
                "candidate_count": len(candidates),
                "selected_trials": rows,
            }
        )
    payload = {
        "protocol_id": config.protocol_id,
        "purpose": (
            "candidate pool for later criterion structuring and synthetic "
            "interactive evaluation; this file contains no eligibility gold"
        ),
        "corpus": {
            **corpus_summary.model_dump(mode="json"),
            "source_path": TEAM_TRIALS_URL,
        },
        "group_count": len(groups),
        "selected_trial_count": len(all_selected),
        "excluded_trial_count": len(excluded_trial_ids),
        "criterion_category_counts": dict(sorted(category_counts.items())),
        "groups": groups,
        "selected_trials": all_selected,
    }
    atomic_write_text(
        destination,
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )
    return payload


__all__ = [
    "ExpansionDiseaseGroup",
    "ExpansionSelectionConfig",
    "select_team_evaluation_trials",
]
