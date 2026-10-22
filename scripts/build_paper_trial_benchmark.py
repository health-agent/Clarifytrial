"""Freeze new public protocol subsets before generating synthetic outcomes.

The eligibility text is authoritative. The selected scalar subset is not the
complete eligibility protocol, and its review here is not clinician annotation.
"""
from __future__ import annotations

import argparse
import json
import random
import re
from pathlib import Path

from clarifytrial.datasets.source_criteria import _numeric_rows
from clarifytrial.datasets.protocol_scope import describe_protocol_scope
from clarifytrial.preparation.team_trials import TeamTrialRecord
from collect_paper_trial_sources import GROUPS


# Semantic ambiguities observed in the retrieved source, before policy scoring.
EXCLUDED_TRIALS = {
    "NCT06791291": "Nested disqualifying laboratory list under an inclusion heading.",
    "NCT06058377": "Different registration and randomization criterion blocks.",
    "NCT06043713": "Different leukapheresis and infusion criterion blocks.",
    "NCT03191149": "Printed hemoglobin 9.0 g/L; cannot silently repair source unit.",
    "NCT07889505": "Healthy-volunteer and disease-specific part requirements differ.",
    "NCT07815236": "Aim-specific curriculum eligibility rather than a universal scalar.",
    "NCT05944016": "Multi-line OR branches combine age, albuminuria and eGFR.",
    "NCT07783802": "BMI is one option in an at-least-one chronic-condition list.",
    "NCT06677060": "eGFR inclusion appears in an alternative risk-factor list.",
    "NCT07890584": "Dry weight is not interchangeable with ordinary body weight.",
}


def build(source: Path, destination: Path):
    prior_ids = set()
    for root in (Path("configs"), Path("data")):
        for path in root.rglob("*.json"):
            if destination in path.parents:
                continue
            prior_ids.update(re.findall(r"NCT\d{8}", path.read_text(encoding="utf-8-sig")))
    used = set()
    groups = []
    for group in GROUPS:
        envelope = json.loads((source / f"{group}.json").read_text(encoding="utf-8"))
        candidates = []
        for rank, study in enumerate(envelope["response"]["studies"]):
            p = study["protocolSection"]
            identity, e = p["identificationModule"], p.get("eligibilityModule", {})
            tid = identity["nctId"]
            if (tid in prior_ids or tid in used or tid in EXCLUDED_TRIALS
                or p.get("designModule", {}).get("studyType") != "INTERVENTIONAL"
                or not e.get("eligibilityCriteria")):
                continue
            record = TeamTrialRecord(nct_id=tid, title=identity["briefTitle"],
                conditions=p["conditionsModule"]["conditions"], eligibility_text=e["eligibilityCriteria"],
                overall_status=p["statusModule"]["overallStatus"])
            if re.search(r"(?im)^\s*(?:part [abc12]|cohort \d|step \d)\b", record.eligibility_text):
                continue  # No flattening of alternative cohorts or phases.
            rows = _numeric_rows(record, group)
            # A subgroup-qualified scalar is not a universal condition.
            rows = [r for r in rows if not re.search(
                r"for (?:participants|patients) with|rolling over|step \d|leukapheresis|randomization",
                r["source_text"], re.I)]
            if not rows:
                continue
            for row in rows:
                if row["fact_code"] == "hemoglobin_g_l":
                    row["original_threshold"] = row["threshold"]
                    row["original_unit"] = row["unit"]
                    row.update(fact_code="hemoglobin_g_dl", unit="g/dL", threshold=row["threshold"] / 10)
            candidates.append({"nct_id": tid, "title": record.title, "conditions": record.conditions,
                "retrieval_rank": rank, "criteria": rows, "eligibility_text": record.eligibility_text,
                "retrieved_at": envelope["retrieved_at"], "source_url": f"https://clinicaltrials.gov/study/{tid}"})
        selected = candidates[:8]
        if len(selected) < 8:
            raise ValueError(f"{group}: only {len(selected)} eligible sources; do not duplicate trials")
        used.update(r["nct_id"] for r in selected)
        random.Random(20261022 + list(GROUPS).index(group)).shuffle(selected)
        for i, trial in enumerate(selected):
            trial["split"] = "development" if i < 4 else "heldout"
            for j, row in enumerate(trial["criteria"]):
                row["criterion_id"] = f"{trial['nct_id']}:numeric:{j + 1}"
            trial["scope"] = describe_protocol_scope(trial)
        groups.append({"group_id": group, "query": GROUPS[group], "trials": selected})
    payload = {"version": "paper-source-subsets-v1", "seed": 20261022,
        "scope": "Selected unconditional numeric conditions only; not complete clinical eligibility. Query strata are not diagnosis adjudications.",
        "selection": "First eight new interventional trials per registry query with a supported scalar; globally deduplicated in query order. Four development and four heldout trial IDs per stratum, split before patient generation.",
        "evidence_assumption": "Fresh verified records within 14 days are a synthetic evidence-quality policy, not an assertion that all source protocols impose this window.",
        "review": "Author source inspection; no independent clinician labels.",
        "excluded_trials": EXCLUDED_TRIALS, "prior_trial_ids": sorted(prior_ids), "groups": groups}
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "protocol_subsets.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"trials":len(used), "criteria":sum(len(t["criteria"]) for g in groups for t in g["trials"]),
        "groups":{g["group_id"]: sorted({r["fact_code"] for t in g["trials"] for r in t["criteria"]}) for g in groups}},indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path("runs/paper-trial-sources-20261022"))
    parser.add_argument("--output", type=Path, default=Path("data/paper_source_benchmark_v1"))
    args = parser.parse_args()
    build(args.source, args.output)
