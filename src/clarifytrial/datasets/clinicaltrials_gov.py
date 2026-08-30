"""Pinned ClinicalTrials.gov source records for the v5 public-condition pilot."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


API_ROOT = "https://clinicaltrials.gov/api/v2"
TERMS_URL = "https://clinicaltrials.gov/about-site/terms-conditions"

CLARIFYTRIAL_V5_NCT_IDS: dict[str, tuple[str, ...]] = {
    "type_2_diabetes": (
        "NCT07026968",
        "NCT06267391",
        "NCT07527650",
        "NCT07146347",
        "NCT05516576",
    ),
    "breast_cancer": (
        "NCT07054242",
        "NCT03546686",
        "NCT07467330",
        "NCT07227233",
        "NCT07441512",
    ),
    "major_depressive_disorder": (
        "NCT05757791",
        "NCT06633016",
        "NCT06820723",
        "NCT07041073",
        "NCT07503002",
    ),
}


def _fetch_json(url: str) -> dict[str, Any]:
    request = Request(url, headers={"User-Agent": "ClarifyTrial-research/0.1"})
    with urlopen(request, timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _eligibility_text(record: dict[str, Any]) -> str:
    try:
        return record["protocolSection"]["eligibilityModule"]["eligibilityCriteria"]
    except KeyError as exc:
        raise ValueError("study record has no eligibility criteria") from exc


def fetch_clinicaltrials_v5_sources(
    cache_dir: str | Path,
    *,
    force: bool = False,
) -> dict[str, Any]:
    """Download the 15 declared public study records and source metadata."""

    destination = Path(cache_dir)
    records_dir = destination / "records"
    version = _fetch_json(f"{API_ROOT}/version")
    retrieved_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for disease_group, nct_ids in CLARIFYTRIAL_V5_NCT_IDS.items():
        for nct_id in nct_ids:
            path = records_dir / f"{nct_id}.json"
            if force or not path.exists():
                record = _fetch_json(f"{API_ROOT}/studies/{nct_id}")
                _write_json(path, record)
            else:
                record = json.loads(path.read_text(encoding="utf-8"))
            actual_id = record.get("protocolSection", {}).get(
                "identificationModule", {}
            ).get("nctId")
            if actual_id != nct_id:
                raise ValueError(f"record ID mismatch for {nct_id}")
            eligibility = _eligibility_text(record)
            rows.append(
                {
                    "disease_group": disease_group,
                    "nct_id": nct_id,
                    "study_url": f"https://clinicaltrials.gov/study/{nct_id}",
                    "api_url": f"{API_ROOT}/studies/{nct_id}",
                    "local_record": str(path),
                    "record_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "eligibility_sha256": hashlib.sha256(
                        eligibility.encode("utf-8")
                    ).hexdigest(),
                }
            )
    metadata = {
        "source": "ClinicalTrials.gov",
        "api_version": version.get("apiVersion"),
        "data_timestamp": version.get("dataTimestamp"),
        "retrieved_at": retrieved_at,
        "terms_url": TERMS_URL,
        "attribution": "ClinicalTrials.gov, U.S. National Library of Medicine",
        "modifications": (
            "원본 연구기록은 수정하지 않았다. 이후 구조화 기준은 원문 일부를 "
            "수치·기간 규칙으로 옮긴 연구자 파생자료다."
        ),
        "study_count": len(rows),
        "studies": rows,
    }
    _write_json(destination / "source_metadata.json", metadata)
    return metadata
