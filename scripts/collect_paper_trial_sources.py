"""Collect new public protocols before constructing or scoring synthetic patients."""

from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from clarifytrial.datasets.source_criteria import _numeric_rows
from clarifytrial.preparation.team_trials import TeamTrialRecord


GROUPS = {
    "type_2_diabetes": "type 2 diabetes mellitus",
    "obesity": "obesity",
    "chronic_kidney_disease": "chronic kidney disease",
    "heart_failure": "heart failure",
    "breast_cancer": "breast cancer",
    "non_small_cell_lung_cancer": "non-small cell lung cancer",
}


def collect(output: Path, *, page_size: int = 100) -> None:
    output.mkdir(parents=True, exist_ok=True)
    known = set()
    for root in (Path("configs"), Path("data")):
        for path in root.rglob("*.json"):
            if any(part.startswith("paper_source_benchmark_v") for part in path.parts):
                continue
            known.update(re.findall(r"NCT\d{8}", path.read_text(encoding="utf-8-sig")))
    rows = []
    for group, query in GROUPS.items():
        url = "https://clinicaltrials.gov/api/v2/studies?" + urlencode({
            "query.cond": query, "filter.overallStatus": "RECRUITING,NOT_YET_RECRUITING",
            "pageSize": page_size, "sort": "LastUpdatePostDate:desc", "format": "json",
        })
        cache = output / f"{group}.json"
        if cache.exists():
            envelope = json.loads(cache.read_text(encoding="utf-8"))
        else:
            request = Request(url, headers={"User-Agent": "ClarifyTrial-research/0.1", "Accept": "application/json"})
            with urlopen(request, timeout=45) as response:
                payload = json.load(response)
            envelope = {"retrieved_at": datetime.now(timezone.utc).isoformat(), "url": url, "response": payload}
            cache.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
        studies = envelope["response"]["studies"]
        candidates = 0
        for position, study in enumerate(studies):
            protocol = study["protocolSection"]
            identity = protocol["identificationModule"]
            eligibility = protocol.get("eligibilityModule", {})
            if (identity["nctId"] in known or protocol.get("designModule", {}).get("studyType") != "INTERVENTIONAL"
                or not eligibility.get("eligibilityCriteria")):
                continue
            record = TeamTrialRecord(
                nct_id=identity["nctId"], title=identity["briefTitle"],
                conditions=protocol.get("conditionsModule", {}).get("conditions", []),
                eligibility_text=eligibility.get("eligibilityCriteria", ""),
                overall_status=protocol["statusModule"]["overallStatus"],
                minimum_age=eligibility.get("minimumAge"), maximum_age=eligibility.get("maximumAge"),
                sex=eligibility.get("sex"),
            )
            numeric = _numeric_rows(record, group)
            if not numeric:
                continue
            candidates += 1
            for criterion in numeric:
                rows.append({
                    "group": group, "retrieval_rank": position,
                    "nct_id": record.nct_id, "title": record.title,
                    "conditions": "; ".join(record.conditions),
                    "source_lines": len([line for line in record.eligibility_text.splitlines() if line.strip()]),
                    "numeric_count": len(numeric),
                    **{key: criterion[key] for key in ("fact_code", "kind", "operator", "threshold", "unit", "source_text", "line_number")},
                })
        print(f"{group}: {len(studies)} sources, {candidates} new numeric candidates", flush=True)
    if rows:
        with (output / "numeric_candidates.csv").open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    print(f"excluded prior trial ids: {len(known)}; numeric rows: {len(rows)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("runs/paper-trial-sources-20261022"))
    args = parser.parse_args()
    collect(args.output)
