"""Build development and final new-trial partitions with a separate gold oracle.

The public-protocol builder prepares source-grounded criteria and synthetic facts.
Expected statuses are then replaced by this file's small reference implementation,
which does not import ClarifyTrial's criterion evaluator or decision aggregator.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from clarifytrial.datasets.source_benchmark import build_source_benchmark
from clarifytrial.io import atomic_write_text


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _canonical_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _partition_selection(
    selection: dict[str, Any],
    *,
    partition: str,
) -> dict[str, Any]:
    selected_by_group: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selection["selected_trials"]:
        selected_by_group[str(row["group_id"])].append(row)

    groups = []
    selected_trials = []
    for group in selection["groups"]:
        group_id = str(group["group_id"])
        rows = selected_by_group[group_id]
        if len(rows) < 2 or len(rows) % 2:
            raise ValueError(
                f"group {group_id!r} needs an even number of selected trials"
            )
        midpoint = len(rows) // 2
        kept = rows[:midpoint] if partition == "development" else rows[midpoint:]
        kept_ids = {str(row["nct_id"]) for row in kept}
        selected_group_rows = [
            row
            for row in group["selected_trials"]
            if str(row["nct_id"]) in kept_ids
        ]
        groups.append(
            {
                **group,
                "candidate_count": len(selected_group_rows),
                "selected_trials": selected_group_rows,
            }
        )
        selected_trials.extend(kept)

    return {
        "protocol_id": f"{selection['protocol_id']}:{partition}",
        "purpose": f"{partition} partition of the frozen new-trial benchmark",
        "corpus": selection["corpus"],
        "group_count": len(groups),
        "selected_trial_count": len(selected_trials),
        "groups": groups,
        "selected_trials": selected_trials,
    }


def _compare(value: float, operator: str, threshold: float) -> bool:
    if operator == "gt":
        return value > threshold
    if operator == "gte":
        return value >= threshold
    if operator == "lt":
        return value < threshold
    if operator == "lte":
        return value <= threshold
    if operator == "eq":
        return value == threshold
    raise ValueError(f"unsupported reference operator: {operator}")


def _reference_decisions(
    *,
    episode: dict[str, Any],
    pair: dict[str, Any],
    criteria_by_trial: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    known = {
        (str(item.get("concept")), str(item.get("unit"))): float(item["value"])
        for item in episode["evidence"]
        if item.get("concept") is not None
        and item.get("unit") is not None
        and item.get("value") is not None
    }
    requests = episode.get("missing_information", [])
    decisions = []
    for trial_id in pair["trial_ids"]:
        criteria = criteria_by_trial[str(trial_id)]
        criterion_ids = {str(row["criterion_id"]) for row in criteria}
        violated = False
        unresolved = False
        for row in criteria:
            key = (f"{row['group_id']}:{row['fact_code']}", str(row["unit"]))
            value = known.get(key)
            if value is None:
                unresolved = True
                continue
            predicate_met = _compare(
                value,
                str(row["operator"]),
                float(row["threshold"]),
            )
            criterion_violated = (
                not predicate_met
                if str(row["kind"]) == "inclusion"
                else predicate_met
            )
            violated = violated or criterion_violated

        pending = sorted(
            str(request["fact_id"])
            for request in requests
            if criterion_ids.intersection(request["related_criterion_ids"])
        )
        if violated:
            candidate_status = "remove"
            confirmation_status = "ineligible"
            logic_status = "violated"
        elif unresolved:
            candidate_status = "retain"
            confirmation_status = "not_confirmed"
            logic_status = "unresolved"
        else:
            candidate_status = "retain"
            confirmation_status = "confirmed"
            logic_status = "satisfied"
        decisions.append(
            {
                "trial_id": str(trial_id),
                "candidate_status": candidate_status,
                "confirmation_status": confirmation_status,
                "pending_fact_ids": pending,
                "logic_status": logic_status,
            }
        )
    return decisions


def _replace_gold(
    *,
    trial_set: dict[str, Any],
    patient_pairs: dict[str, Any],
    partition: str,
    frozen_at: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    criteria_by_trial: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for criterion in trial_set["criteria"]:
        criteria_by_trial[str(criterion["nct_id"])].append(criterion)

    label_rows = []
    full_decisions = []
    initial_decisions = []
    for pair in patient_pairs["pairs"]:
        pair["split"] = "development" if partition == "development" else "heldout"
        full = _reference_decisions(
            episode=pair["sufficient_evidence_episode"],
            pair=pair,
            criteria_by_trial=criteria_by_trial,
        )
        initial = _reference_decisions(
            episode=pair["insufficient_evidence_episode"],
            pair=pair,
            criteria_by_trial=criteria_by_trial,
        )
        pair["sufficient_evidence_episode"]["expected_trial_decisions"] = full
        pair["insufficient_evidence_episode"]["expected_trial_decisions"] = initial
        full_by_trial = {row["trial_id"]: row for row in full}
        initial_by_trial = {row["trial_id"]: row for row in initial}
        pair["expected_pair_relation"] = {
            "same_clinical_values": True,
            "candidate_changed_trial_ids": sorted(
                trial_id
                for trial_id in pair["trial_ids"]
                if full_by_trial[trial_id]["candidate_status"]
                != initial_by_trial[trial_id]["candidate_status"]
            ),
            "confirmation_changed_trial_ids": sorted(
                trial_id
                for trial_id in pair["trial_ids"]
                if full_by_trial[trial_id]["confirmation_status"]
                != initial_by_trial[trial_id]["confirmation_status"]
            ),
            "all_missing_answers_are_declared": True,
        }
        for episode_name, decisions in (("complete", full), ("initial", initial)):
            label_rows.extend(
                {
                    "patient_id": pair["patient_id"],
                    "episode": episode_name,
                    **row,
                }
                for row in decisions
            )
        full_decisions.extend(full)
        initial_decisions.extend(initial)

    patient_count = len(patient_pairs["pairs"])
    patient_pairs["development_patient_count"] = (
        patient_count if partition == "development" else 0
    )
    patient_pairs["heldout_patient_count"] = (
        patient_count if partition == "final" else 0
    )
    patient_pairs["initial_retained_not_confirmed_count"] = sum(
        row["candidate_status"] == "retain"
        and row["confirmation_status"] == "not_confirmed"
        for row in initial_decisions
    )
    patient_pairs["complete_confirmed_candidate_count"] = sum(
        row["candidate_status"] == "retain"
        and row["confirmation_status"] == "confirmed"
        for row in full_decisions
    )
    patient_pairs["complete_ineligible_count"] = sum(
        row["candidate_status"] == "remove"
        and row["confirmation_status"] == "ineligible"
        for row in full_decisions
    )

    trial_ids = sorted(str(row["nct_id"]) for row in trial_set["trials"])
    patient_ids = sorted(str(row["patient_id"]) for row in patient_pairs["pairs"])
    gold = {
        "protocol_id": "clarifytrial-independent-structured-gold-v1",
        "benchmark_protocol_id": trial_set["protocol_id"],
        "partition": partition,
        "frozen_at": frozen_at,
        "independent_from_runtime_evaluator": True,
        "frozen_before_final_run": True,
        "authoring_method": (
            "A separate serialized-operator reference implementation created this "
            "table without importing ClarifyTrial's criterion evaluator or trial "
            "decision aggregator. The runtime reads the frozen table only."
        ),
        "trial_ids_sha256": _canonical_sha256(trial_ids),
        "patient_ids_sha256": _canonical_sha256(patient_ids),
        "label_count": len(label_rows),
        "labels": label_rows,
    }
    gold_sha256 = _canonical_sha256(gold)
    gold_metadata = {
        "description": (
            "공개 원문에서 옮긴 객관적 조건과 합성 수치를 별도 계산표로 비교해 "
            "기대 결과를 고정했다. 현재 실행 판정 코드는 이 표를 만들지 않는다."
        ),
        "independent_from_runtime_evaluator": True,
        "frozen_before_final_run": True,
        "partition": partition,
        "frozen_at": frozen_at,
        "gold_sha256": gold_sha256,
        "label_count": len(label_rows),
    }
    trial_set["status"] = "independent_new_trial_benchmark"
    trial_set["benchmark_partition"] = partition
    trial_set["gold_standard"] = gold_metadata
    for trial in trial_set["trials"]:
        trial["benchmark_partition"] = partition
    patient_pairs["status"] = "independent_new_trial_synthetic_patients"
    patient_pairs["benchmark_partition"] = partition
    patient_pairs["gold_standard"] = gold_metadata
    patient_pairs["authority"] = (
        "Frozen answers for the declared structured criteria and synthetic values; "
        "not a label for each trial's complete clinical eligibility protocol."
    )
    return trial_set, patient_pairs, gold


def build_benchmark(
    *,
    config_path: Path,
    selection_path: Path,
    corpus_path: Path,
    output_dir: Path,
    frozen_at: str,
    overwrite: bool = False,
) -> None:
    selection = _read(selection_path)
    partitions = {
        name: _partition_selection(selection, partition=name)
        for name in ("development", "final")
    }
    development_ids = {
        row["nct_id"] for row in partitions["development"]["selected_trials"]
    }
    final_ids = {row["nct_id"] for row in partitions["final"]["selected_trials"]}
    if development_ids & final_ids:
        raise ValueError("development and final trial IDs overlap")

    for partition, partition_selection in partitions.items():
        destination = output_dir / partition
        if destination.exists() and not overwrite:
            raise FileExistsError(f"benchmark partition already exists: {destination}")
        with tempfile.TemporaryDirectory(prefix="clarifytrial-independent-") as raw:
            temporary = Path(raw)
            temporary_selection = temporary / "selection.json"
            atomic_write_text(
                temporary_selection,
                json.dumps(partition_selection, ensure_ascii=False, indent=2) + "\n",
            )
            build_source_benchmark(
                config_path=config_path,
                selection_path=temporary_selection,
                corpus_path=corpus_path,
                output_dir=temporary / "base",
            )
            trial_set = _read(temporary / "base" / "trial_set.json")
            patient_pairs = _read(temporary / "base" / "patient_pairs.json")
        trial_set, patient_pairs, gold = _replace_gold(
            trial_set=trial_set,
            patient_pairs=patient_pairs,
            partition=partition,
            frozen_at=frozen_at,
        )
        destination.mkdir(parents=True, exist_ok=overwrite)
        for name, value in (
            ("selection.json", partition_selection),
            ("trial_set.json", trial_set),
            ("patient_pairs.json", patient_pairs),
            ("gold_labels.json", gold),
        ):
            atomic_write_text(
                destination / name,
                json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--selection", required=True, type=Path)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--frozen-at", required=True)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()
    build_benchmark(
        config_path=args.config,
        selection_path=args.selection,
        corpus_path=args.corpus,
        output_dir=args.output,
        frozen_at=args.frozen_at,
        overwrite=args.overwrite,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
