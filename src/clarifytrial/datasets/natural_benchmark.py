"""Build the frozen, preliminary natural-input evaluation set."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .integrity import portable_text_sha256
from .natural_evaluation import load_natural_evaluation_selection_config


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"JSON document must be an object: {path}")
    return payload


def _sha256(path: Path) -> str:
    return portable_text_sha256(path)


def _coverage_by_trial(gold: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = gold.get("trial_coverage")
    if not isinstance(rows, list):
        raise ValueError("conservative gold must contain trial_coverage")
    result: dict[str, Mapping[str, Any]] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError("trial coverage rows must be objects")
        nct_id = row.get("nct_id")
        if not isinstance(nct_id, str) or not nct_id:
            raise ValueError("every trial coverage row needs an NCT ID")
        if nct_id in result:
            raise ValueError(f"trial coverage repeats {nct_id}")
        result[nct_id] = row
    return result


def build_natural_evaluation_trial_set(
    *,
    primary_source_path: str | Path,
    reserve_source_path: str | Path,
    primary_gold_path: str | Path,
    reserve_gold_path: str | Path,
    selection_config_path: str | Path,
    output_path: str | Path,
) -> dict[str, Any]:
    """Replace low-coverage primaries using frozen reserve order."""

    primary_source_path = Path(primary_source_path)
    reserve_source_path = Path(reserve_source_path)
    primary_gold_path = Path(primary_gold_path)
    reserve_gold_path = Path(reserve_gold_path)
    destination = Path(output_path)
    if destination.exists():
        raise FileExistsError("final preliminary trial set already exists")

    primary_source = _read_json(primary_source_path)
    reserve_source = _read_json(reserve_source_path)
    primary_gold = _read_json(primary_gold_path)
    reserve_gold = _read_json(reserve_gold_path)
    config = load_natural_evaluation_selection_config(selection_config_path)

    primary_digest = _sha256(primary_source_path)
    reserve_digest = _sha256(reserve_source_path)
    if primary_gold.get("source_sha256") != primary_digest:
        raise ValueError("primary gold does not match its source review")
    if reserve_gold.get("source_sha256") != reserve_digest:
        raise ValueError("reserve gold does not match its source review")

    primary_trials = primary_source.get("trials")
    reserve_trials = reserve_source.get("reserve_trials")
    if not isinstance(primary_trials, list) or not isinstance(reserve_trials, list):
        raise ValueError("source documents do not contain the expected trial lists")
    primary_coverage = _coverage_by_trial(primary_gold)
    reserve_coverage = _coverage_by_trial(reserve_gold)
    metadata_by_id = {
        str(item["nct_id"]): item for item in [*primary_trials, *reserve_trials]
    }

    selected_trials: list[dict[str, Any]] = []
    group_summaries = []
    selected_ids: set[str] = set()
    for group in config.groups:
        group_primaries = [
            item for item in primary_trials if item.get("group_id") == group.group_id
        ]
        group_reserves = [
            item for item in reserve_trials if item.get("group_id") == group.group_id
        ]
        if len(group_primaries) != group.target_count:
            raise ValueError(f"primary count differs for {group.group_id}")
        eligible_reserves = [
            item
            for item in group_reserves
            if bool(reserve_coverage.get(str(item.get("nct_id")), {}).get("meets_minimum"))
        ]
        reserve_position = 0
        replacements = []
        group_selected = []
        for slot, primary in enumerate(group_primaries, start=1):
            primary_id = str(primary["nct_id"])
            selected = primary
            origin = "primary"
            replaced_nct_id = None
            if not bool(primary_coverage.get(primary_id, {}).get("meets_minimum")):
                if reserve_position >= len(eligible_reserves):
                    raise ValueError(
                        f"not enough eligible reserve trials for {group.group_id}"
                    )
                selected = eligible_reserves[reserve_position]
                reserve_position += 1
                origin = "reserve_replacement"
                replaced_nct_id = primary_id
                replacements.append(
                    {
                        "removed_nct_id": primary_id,
                        "selected_nct_id": str(selected["nct_id"]),
                    }
                )
            selected_id = str(selected["nct_id"])
            if selected_id in selected_ids:
                raise ValueError(f"trial was selected twice: {selected_id}")
            selected_ids.add(selected_id)
            coverage = (
                primary_coverage if origin == "primary" else reserve_coverage
            )[selected_id]
            row = {
                "group_id": group.group_id,
                "group_label": group.label,
                "selection_slot": slot,
                "nct_id": selected_id,
                "title": selected.get("title"),
                "study_url": selected.get("study_url"),
                "selection_origin": origin,
                "replaced_nct_id": replaced_nct_id,
                "accepted_source_line_count": coverage.get(
                    "accepted_source_line_count"
                ),
                "criterion_count": coverage.get("criterion_count"),
            }
            selected_trials.append(row)
            group_selected.append(selected_id)
        group_summaries.append(
            {
                "group_id": group.group_id,
                "group_label": group.label,
                "target_count": group.target_count,
                "selected_nct_ids": group_selected,
                "replacement_count": len(replacements),
                "replacements": replacements,
            }
        )

    primary_criteria = primary_gold.get("criteria")
    reserve_criteria = reserve_gold.get("criteria")
    if not isinstance(primary_criteria, list) or not isinstance(reserve_criteria, list):
        raise ValueError("conservative gold must contain criteria lists")
    criteria = [
        row
        for row in [*primary_criteria, *reserve_criteria]
        if row.get("nct_id") in selected_ids
    ]
    criterion_counts = {
        nct_id: sum(row.get("nct_id") == nct_id for row in criteria)
        for nct_id in selected_ids
    }
    for trial in selected_trials:
        nct_id = trial["nct_id"]
        if criterion_counts[nct_id] != trial["criterion_count"]:
            raise ValueError(f"criterion count differs for {nct_id}")
        if criterion_counts[nct_id] < config.minimum_objective_lines:
            raise ValueError(f"selected trial has too few criteria: {nct_id}")
        if nct_id not in metadata_by_id:
            raise ValueError(f"source metadata is missing for {nct_id}")

    payload = {
        "status": "preliminary_ai_reviewed_trial_set",
        "authority": (
            "AI-generated research draft for synthetic evaluation; not physician "
            "gold and not independent two-person consensus"
        ),
        "selection_rule": (
            "Keep each frozen primary that has at least the configured minimum "
            "accepted source lines; otherwise use the first qualifying frozen "
            "reserve in that disease group"
        ),
        "selection_config": str(selection_config_path),
        "minimum_accepted_source_lines_per_trial": config.minimum_objective_lines,
        "primary_source_sha256": primary_digest,
        "reserve_source_sha256": reserve_digest,
        "primary_gold_sha256": _sha256(primary_gold_path),
        "reserve_gold_sha256": _sha256(reserve_gold_path),
        "group_count": len(group_summaries),
        "trial_count": len(selected_trials),
        "criterion_count": len(criteria),
        "replacement_count": sum(
            item["replacement_count"] for item in group_summaries
        ),
        "groups": group_summaries,
        "trials": selected_trials,
        "criteria": criteria,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "output": str(destination),
        "trial_count": len(selected_trials),
        "criterion_count": len(criteria),
        "replacement_count": payload["replacement_count"],
        "groups": group_summaries,
    }


__all__ = ["build_natural_evaluation_trial_set"]
