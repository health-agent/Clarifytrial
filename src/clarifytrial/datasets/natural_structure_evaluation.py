"""Evaluate extraction of values and evidence state from synthetic records."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import EvidenceSourceType, VerificationStatus
from ..llm import ModelCall, ModelUsage, StructuredModel
from ..measurements import units_equivalent
from .integrity import portable_text_sha256


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ExtractedMeasurement(_StrictModel):
    measurement_id: str = Field(min_length=1)
    value: float = Field(allow_inf_nan=False)
    unit: str = Field(min_length=1)
    source_type: EvidenceSourceType
    verification_status: VerificationStatus


class ExtractedNaturalRecord(_StrictModel):
    facts: list[ExtractedMeasurement]


def _record_hash(record: Mapping[str, Any]) -> str:
    payload = {
        "record_id": record["record_id"],
        "record_text": record["record_text"],
        "measurement_catalog": record["measurement_catalog"],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _model_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "record_id": record["record_id"],
        "synthetic_record": record["record_text"],
        "allowed_measurements": record["measurement_catalog"],
    }


def score_extracted_natural_record(
    *, record: Mapping[str, Any], output: ExtractedNaturalRecord
) -> dict[str, Any]:
    expected = {item["measurement_id"]: item for item in record["expected_facts"]}
    predicted: dict[str, ExtractedMeasurement] = {}
    duplicate_ids: set[str] = set()
    for item in output.facts:
        if item.measurement_id in predicted:
            duplicate_ids.add(item.measurement_id)
        else:
            predicted[item.measurement_id] = item
    unknown_ids = sorted(set(predicted) - set(expected))
    missing_ids = sorted(set(expected) - set(predicted))
    per_fact = []
    fully_correct = 0
    critical_correct = 0
    critical_total = 0
    pivotal = set(record["pivotal_fact_codes"])
    for item_id, gold in sorted(expected.items()):
        prediction = predicted.get(item_id)
        value_ok = (
            prediction is not None
            and abs(prediction.value - float(gold["value"])) <= 1e-6
        )
        unit_ok = (
            prediction is not None and units_equivalent(prediction.unit, gold["unit"])
        )
        source_ok = (
            prediction is not None
            and prediction.source_type.value == gold["source_type"]
        )
        status_ok = (
            prediction is not None
            and prediction.verification_status.value
            == gold["verification_status"]
        )
        item_correct = bool(value_ok and unit_ok and source_ok and status_ok)
        fully_correct += item_correct
        is_critical = gold["fact_code"] in pivotal
        if is_critical:
            critical_total += 1
            critical_correct += item_correct
        per_fact.append(
            {
                "measurement_id": item_id,
                "fact_code": gold["fact_code"],
                "pivotal": is_critical,
                "found": prediction is not None,
                "value_correct": value_ok,
                "unit_correct": unit_ok,
                "source_type_correct": source_ok,
                "verification_status_correct": status_ok,
                "fully_correct": item_correct,
                "expected": {
                    "value": gold["value"],
                    "unit": gold["unit"],
                    "source_type": gold["source_type"],
                    "verification_status": gold["verification_status"],
                },
                "predicted": prediction.model_dump(mode="json") if prediction else None,
            }
        )
    expected_count = len(expected)
    prediction_count = len(output.facts)
    return {
        "expected_fact_count": expected_count,
        "predicted_fact_count": prediction_count,
        "found_fact_count": expected_count - len(missing_ids),
        "fully_correct_fact_count": fully_correct,
        "critical_fact_count": critical_total,
        "critical_fully_correct_count": critical_correct,
        "unknown_measurement_ids": unknown_ids,
        "duplicate_measurement_ids": sorted(duplicate_ids),
        "missing_measurement_ids": missing_ids,
        "fact_recall": (expected_count - len(missing_ids)) / expected_count,
        "fact_precision": (
            (len(set(predicted) & set(expected)) / len(predicted)) if predicted else 0.0
        ),
        "fully_correct_fact_rate": fully_correct / expected_count,
        "critical_fully_correct_rate": (
            critical_correct / critical_total if critical_total else 1.0
        ),
        "exact_record_match": (
            fully_correct == expected_count
            and not unknown_ids
            and not duplicate_ids
            and prediction_count == expected_count
        ),
        "per_fact": per_fact,
    }


def _usage_payload(usage: ModelUsage) -> dict[str, Any]:
    return asdict(usage)


def _aggregate_results(results: list[Mapping[str, Any]]) -> dict[str, Any]:
    completed = [item for item in results if item["status"] == "completed"]
    total_expected = sum(item["scores"]["expected_fact_count"] for item in completed)
    total_found = sum(item["scores"]["found_fact_count"] for item in completed)
    total_correct = sum(
        item["scores"]["fully_correct_fact_count"] for item in completed
    )
    total_critical = sum(item["scores"]["critical_fact_count"] for item in completed)
    critical_correct = sum(
        item["scores"]["critical_fully_correct_count"] for item in completed
    )
    predicted_unique = sum(
        item["scores"]["predicted_fact_count"]
        - len(item["scores"]["duplicate_measurement_ids"])
        for item in completed
    )
    known_predicted = sum(
        item["scores"]["found_fact_count"] for item in completed
    )
    fields = (
        "input_tokens",
        "output_tokens",
        "thinking_tokens",
        "total_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    usage = {
        field: sum(
            int(item["usage"].get(field) or 0)
            for item in completed
        )
        for field in fields
    }
    return {
        "requested_record_count": len(results),
        "completed_record_count": len(completed),
        "failed_record_count": len(results) - len(completed),
        "fact_recall": total_found / total_expected if total_expected else 0.0,
        "fact_precision": known_predicted / predicted_unique if predicted_unique else 0.0,
        "fully_correct_fact_rate": (
            total_correct / total_expected if total_expected else 0.0
        ),
        "critical_fully_correct_rate": (
            critical_correct / total_critical if total_critical else 0.0
        ),
        "exact_record_match_rate": (
            sum(item["scores"]["exact_record_match"] for item in completed)
            / len(completed)
            if completed
            else 0.0
        ),
        "unknown_measurement_count": sum(
            len(item["scores"]["unknown_measurement_ids"]) for item in completed
        ),
        "duplicate_measurement_count": sum(
            len(item["scores"]["duplicate_measurement_ids"]) for item in completed
        ),
        "token_usage": usage,
    }


def run_natural_record_structure_evaluation(
    *,
    records_path: str | Path,
    destination: str | Path,
    model: StructuredModel,
    split: Literal["development", "heldout", "all"] = "all",
    evidence_state: Literal["sufficient", "insufficient", "all"] = "all",
    max_workers: int = 3,
) -> dict[str, Any]:
    """Run resumable, concurrent record extraction against one frozen split."""

    if max_workers <= 0:
        raise ValueError("max_workers must be positive")
    records_path = Path(records_path)
    destination = Path(destination)
    source = json.loads(records_path.read_text(encoding="utf-8"))
    prompt_path = (
        Path(__file__).resolve().parents[3]
        / "prompts"
        / "natural_evaluation_record_extractor.md"
    )
    prompt_sha256 = portable_text_sha256(prompt_path)
    selected = [
        item
        for item in source["records"]
        if split == "all" or item["split"] == split
        if evidence_state == "all" or item["evidence_state"] == evidence_state
    ]
    previous: dict[str, Mapping[str, Any]] = {}
    if destination.is_file():
        old = json.loads(destination.read_text(encoding="utf-8"))
        if (
            old.get("records_sha256") == portable_text_sha256(records_path)
            and old.get("prompt_sha256") == prompt_sha256
            and old.get("split") == split
            and old.get("evidence_state") == evidence_state
        ):
            previous = {
                str(item["record_id"]): item
                for item in old.get("results", [])
                if item.get("status") == "completed"
            }
    results: dict[str, dict[str, Any]] = {}
    pending = []
    for record in selected:
        existing = previous.get(str(record["record_id"]))
        if existing and existing.get("record_hash") == _record_hash(record):
            results[str(record["record_id"])] = dict(existing)
        else:
            pending.append(record)
    write_lock = threading.Lock()

    def evaluate(record: Mapping[str, Any]) -> dict[str, Any]:
        try:
            output, usage = model.complete(
                ModelCall(
                    role="natural_record_extractor",
                    prompt_id="prompts/natural_evaluation_record_extractor.md",
                    payload=_model_payload(record),
                    response_model=ExtractedNaturalRecord,
                )
            )
            return {
                "record_id": record["record_id"],
                "episode_id": record["episode_id"],
                "patient_id": record["patient_id"],
                "group_id": record["group_id"],
                "split": record["split"],
                "evidence_state": record["evidence_state"],
                "style": record["style"],
                "record_hash": _record_hash(record),
                "status": "completed",
                "output": output.model_dump(mode="json"),
                "scores": score_extracted_natural_record(
                    record=record, output=output
                ),
                "usage": _usage_payload(usage),
            }
        except Exception as error:
            return {
                "record_id": record["record_id"],
                "episode_id": record["episode_id"],
                "patient_id": record["patient_id"],
                "group_id": record["group_id"],
                "split": record["split"],
                "evidence_state": record["evidence_state"],
                "style": record["style"],
                "record_hash": _record_hash(record),
                "status": "failed",
                "error_type": type(error).__name__,
            }

    def checkpoint() -> None:
        ordered = [results[str(item["record_id"])] for item in selected if str(item["record_id"]) in results]
        payload = {
            "protocol_id": "clarifytrial-natural-record-structure-v1",
            "authority": source["authority"],
            "medical_data_notice": source["medical_data_notice"],
            "medical_disclaimer": source["medical_disclaimer"],
            "records_sha256": portable_text_sha256(records_path),
            "prompt_sha256": prompt_sha256,
            "split": split,
            "evidence_state": evidence_state,
            "summary": _aggregate_results(ordered),
            "results": ordered,
        }
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)

    if pending:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_record = {executor.submit(evaluate, item): item for item in pending}
            for future in as_completed(future_to_record):
                result = future.result()
                with write_lock:
                    results[str(result["record_id"])] = result
                    checkpoint()
    else:
        checkpoint()
    document = json.loads(destination.read_text(encoding="utf-8"))
    return document["summary"]


__all__ = [
    "ExtractedMeasurement",
    "ExtractedNaturalRecord",
    "run_natural_record_structure_evaluation",
    "score_extracted_natural_record",
]
