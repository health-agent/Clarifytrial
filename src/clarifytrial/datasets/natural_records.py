"""Render paired synthetic evidence as readable patient records.

The renderer never invents clinical facts.  It changes only presentation:
half of the profiles use a compact record layout and half use prose.  The
sufficient/insufficient members of a pair keep the same values and ordering.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Any

from ..contracts import EvidenceFact, EvidenceSourceType, VerificationStatus
from ..measurements import normalized_unit
from .integrity import portable_text_sha256


_SYNTHETIC_NOTICE = "All patient records in this file are synthetic."


def measurement_id(fact: EvidenceFact) -> str:
    if fact.concept is None or fact.unit is None:
        raise ValueError("natural evaluation facts must contain concept and unit")
    fact_code = fact.concept.rsplit(":", 1)[-1]
    return f"{fact_code}|{normalized_unit(fact.unit)}"


def _display_value(fact: EvidenceFact) -> str:
    if fact.value is None or fact.unit is None:
        raise ValueError("natural evaluation facts must contain numeric values")
    if normalized_unit(fact.unit) == "bool":
        return "yes" if fact.value == 1 else "no"
    value = str(int(fact.value)) if fact.value.is_integer() else f"{fact.value:g}"
    return f"{value} {fact.unit}"


def _source_phrase(fact: EvidenceFact) -> str:
    phrases = {
        (EvidenceSourceType.MEDICAL_RECORD, VerificationStatus.VERIFIED): (
            "verified medical record"
        ),
        (EvidenceSourceType.OFFICIAL_VERIFICATION, VerificationStatus.VERIFIED): (
            "verified study-site result"
        ),
        (EvidenceSourceType.PATIENT_REPORT, VerificationStatus.REPORTED): (
            "patient report, not yet checked against the record"
        ),
        (EvidenceSourceType.PATIENT_REPORT, VerificationStatus.PENDING): (
            "patient answer still pending confirmation"
        ),
        (EvidenceSourceType.PATIENT_REPORT, VerificationStatus.CONFLICTING): (
            "conflicting patient report"
        ),
    }
    try:
        return phrases[(fact.source_type, fact.verification_status)]
    except KeyError:
        return f"{fact.source_type.value}, {fact.verification_status.value}"


def _fact_description(statement: str) -> str:
    cleaned = statement.removeprefix("합성 환자 ")
    return cleaned.rsplit(":", 1)[0].strip()


def _ordered_facts(facts: list[EvidenceFact], patient_id: str) -> list[EvidenceFact]:
    return sorted(
        facts,
        key=lambda item: hashlib.sha256(
            f"{patient_id}:{measurement_id(item)}".encode("utf-8")
        ).hexdigest(),
    )


def _render_record(
    *,
    patient_id: str,
    episode_id: str,
    style: str,
    facts: list[EvidenceFact],
) -> str:
    lines = [
        "SYNTHETIC RESEARCH RECORD — NOT FOR CLINICAL USE",
        f"Patient: {patient_id}",
        "",
    ]
    for fact in _ordered_facts(facts, patient_id):
        event_date = fact.event_date or fact.recorded_date
        when = event_date.isoformat() if isinstance(event_date, date) else "date unavailable"
        description = _fact_description(fact.statement)
        value = _display_value(fact)
        source = _source_phrase(fact)
        if style == "record_entries":
            lines.append(f"{when} | {source} | {description}: {value}.")
        elif style == "narrative_note":
            lines.append(
                f"On {when}, {description.lower()} was {value}; source: {source}."
            )
        else:
            raise ValueError(f"unsupported natural record style: {style}")
    lines.extend(
        [
            "",
            "This synthetic record is for software research only. Trial eligibility "
            "must be checked by the study team against the current protocol and the "
            "complete patient record.",
        ]
    )
    return "\n".join(lines)


def _episode_record(
    *,
    pair: Mapping[str, Any],
    episode_key: str,
    style: str,
) -> dict[str, Any]:
    episode = pair[episode_key]
    facts = [EvidenceFact.model_validate(item) for item in episode["evidence"]]
    expected = []
    catalog = []
    for fact in facts:
        item_id = measurement_id(fact)
        fact_code = fact.concept.rsplit(":", 1)[-1] if fact.concept else ""
        catalog.append(
            {
                "measurement_id": item_id,
                "fact_code": fact_code,
                "description": _fact_description(fact.statement),
                "expected_unit": fact.unit,
            }
        )
        expected.append(
            {
                "measurement_id": item_id,
                "fact_code": fact_code,
                "value": fact.value,
                "unit": fact.unit,
                "source_type": fact.source_type.value,
                "verification_status": fact.verification_status.value,
                "event_date": fact.event_date.isoformat() if fact.event_date else None,
                "recorded_date": (
                    fact.recorded_date.isoformat() if fact.recorded_date else None
                ),
            }
        )
    episode_id = str(episode["episode_id"])
    return {
        "record_id": f"record:{episode_id}",
        "episode_id": episode_id,
        "patient_id": pair["patient_id"],
        "group_id": pair["group_id"],
        "split": pair["split"],
        "evidence_state": (
            "sufficient" if episode_key.startswith("sufficient") else "insufficient"
        ),
        "style": style,
        "record_text": _render_record(
            patient_id=str(pair["patient_id"]),
            episode_id=episode_id,
            style=style,
            facts=facts,
        ),
        "measurement_catalog": sorted(
            catalog, key=lambda item: item["measurement_id"]
        ),
        "expected_facts": sorted(
            expected, key=lambda item: item["measurement_id"]
        ),
        "trial_ids": pair["trial_ids"],
        "pivotal_fact_codes": pair["pivotal_fact_codes"],
        "expected_trial_decisions": episode["expected_trial_decisions"],
    }


def build_natural_evaluation_records(
    *, patient_pairs_path: str | Path, destination: str | Path
) -> dict[str, Any]:
    patient_pairs_path = Path(patient_pairs_path)
    destination = Path(destination)
    pairs_document = json.loads(patient_pairs_path.read_text(encoding="utf-8"))
    records = []
    for pair in pairs_document["pairs"]:
        profile_number = int(str(pair["patient_id"]).rsplit("-", 1)[-1])
        style = "record_entries" if profile_number % 2 else "narrative_note"
        records.append(
            _episode_record(
                pair=pair,
                episode_key="sufficient_evidence_episode",
                style=style,
            )
        )
        records.append(
            _episode_record(
                pair=pair,
                episode_key="insufficient_evidence_episode",
                style=style,
            )
        )
    payload = {
        "status": "preliminary_ai_authored_synthetic_evaluation",
        "authority": pairs_document["authority"],
        "medical_data_notice": _SYNTHETIC_NOTICE,
        "medical_disclaimer": pairs_document["medical_disclaimer"],
        "patient_pairs_sha256": portable_text_sha256(patient_pairs_path),
        "rendering_rule": (
            "Clinical values are copied without change. Odd profile numbers use "
            "record entries; even profile numbers use prose. Pair members keep "
            "the same fact order and differ only in evidence source or status."
        ),
        "record_count": len(records),
        "development_record_count": sum(
            item["split"] == "development" for item in records
        ),
        "heldout_record_count": sum(item["split"] == "heldout" for item in records),
        "records": records,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "output": str(destination),
        "record_count": payload["record_count"],
        "development_record_count": payload["development_record_count"],
        "heldout_record_count": payload["heldout_record_count"],
    }


def audit_natural_evaluation_records(
    *, patient_pairs_path: str | Path, records_path: str | Path
) -> dict[str, Any]:
    patient_pairs_path = Path(patient_pairs_path)
    records_path = Path(records_path)
    document = json.loads(records_path.read_text(encoding="utf-8"))
    if document.get("medical_data_notice") != _SYNTHETIC_NOTICE:
        raise ValueError("synthetic patient notice is missing")
    if document.get("patient_pairs_sha256") != portable_text_sha256(
        patient_pairs_path
    ):
        raise ValueError("natural records do not match patient pairs")
    records = document.get("records")
    if not isinstance(records, list):
        raise ValueError("natural record document must contain records")
    record_ids = [str(item["record_id"]) for item in records]
    if len(record_ids) != len(set(record_ids)):
        raise ValueError("record IDs must be unique")
    by_episode = {str(item["episode_id"]): item for item in records}
    pairs = json.loads(patient_pairs_path.read_text(encoding="utf-8"))["pairs"]
    expected_count = 0
    for pair in pairs:
        pair_records = []
        for episode_key in (
            "sufficient_evidence_episode",
            "insufficient_evidence_episode",
        ):
            episode = pair[episode_key]
            record = by_episode.get(str(episode["episode_id"]))
            if record is None:
                raise ValueError(f"missing record for {episode['episode_id']}")
            expected_count += 1
            source_facts = {
                measurement_id(EvidenceFact.model_validate(item)): (
                    item["value"],
                    normalized_unit(item["unit"]),
                    item["source_type"],
                    item["verification_status"],
                )
                for item in episode["evidence"]
            }
            rendered_facts = {
                item["measurement_id"]: (
                    item["value"],
                    normalized_unit(item["unit"]),
                    item["source_type"],
                    item["verification_status"],
                )
                for item in record["expected_facts"]
            }
            if rendered_facts != source_facts:
                raise ValueError(f"rendered facts differ for {episode['episode_id']}")
            pair_records.append(record)
        left_values = {
            item["measurement_id"]: (item["value"], normalized_unit(item["unit"]))
            for item in pair_records[0]["expected_facts"]
        }
        right_values = {
            item["measurement_id"]: (item["value"], normalized_unit(item["unit"]))
            for item in pair_records[1]["expected_facts"]
        }
        if left_values != right_values:
            raise ValueError(f"pair values differ for {pair['patient_id']}")
        if pair_records[0]["style"] != pair_records[1]["style"]:
            raise ValueError(f"pair styles differ for {pair['patient_id']}")
    if len(records) != expected_count or document.get("record_count") != expected_count:
        raise ValueError("natural record count differs")
    return {
        "passed": True,
        "record_count": expected_count,
        "patient_count": len(pairs),
        "pair_value_mismatch_count": 0,
        "synthetic_notice_present": True,
    }


__all__ = [
    "audit_natural_evaluation_records",
    "build_natural_evaluation_records",
    "measurement_id",
]
