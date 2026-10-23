"""Fix unused public protocol subsets before running the new method.

Reuse the original dated registry responses, not previous selected protocols.
Select the four richest supported unconditional scalar subsets per query, with
retrieval-rank ties. This is purpose selection, not clinical random sampling.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from clarifytrial.datasets.source_criteria import _numeric_rows
from clarifytrial.datasets.protocol_scope import describe_protocol_scope
from clarifytrial.preparation.team_trials import TeamTrialRecord
from build_paper_trial_benchmark import EXCLUDED_TRIALS
from collect_paper_trial_sources import GROUPS


def build(source, output):
    prior = set()
    for root in (Path("configs"), Path("data")):
        for p in root.rglob("*.json"):
            if output in p.parents: continue
            prior.update(re.findall(r"NCT\d{8}", p.read_text(encoding="utf-8-sig")))
    for p in (Path("docs/internal/results/leaf-protocol-v1/cases.json"),
              Path("docs/internal/results/leaf-protocol-holdout-v1/cases.json")):
        prior.update(c["nct_id"] for c in json.loads(p.read_text(encoding="utf-8"))["cases"])
    used = set(); groups = []
    for name in GROUPS:
        envelope = json.loads((source/f"{name}.json").read_text(encoding="utf-8"))
        candidates = []
        for rank, study in enumerate(envelope["response"]["studies"]):
            p=study["protocolSection"]; identity=p["identificationModule"]
            tid=identity["nctId"]; text=p.get("eligibilityModule",{}).get("eligibilityCriteria","")
            if tid in prior or tid in used or tid in EXCLUDED_TRIALS or not text:
                continue
            if p.get("designModule",{}).get("studyType") != "INTERVENTIONAL": continue
            if re.search(r"(?im)^\s*(?:part [abc12]|cohort \d|step \d)\b", text): continue
            record=TeamTrialRecord(nct_id=tid,title=identity["briefTitle"],
                conditions=p.get("conditionsModule",{}).get("conditions",[]),eligibility_text=text,
                overall_status=p["statusModule"]["overallStatus"])
            rows=[r for r in _numeric_rows(record,name) if not re.search(
                r"for (?:participants|patients) with|rolling over|step \d|leukapheresis|randomization|\bunless\b|\bexcept\b",
                r["source_text"],re.I)]
            # Mixed scientific notation and parenthetical per-volume units in
            # this raw clause bind the first number to the wrong unit. Do not
            # silently repair the source/extractor before policy evaluation.
            rejected=[]
            if tid == "NCT04585490":
                rejected=[r for r in rows if r["fact_code"].startswith("absolute_neutrophil")]
                rows=[r for r in rows if r not in rejected]
            if not rows: continue
            for j,row in enumerate(rows):
                if row["fact_code"] == "hemoglobin_g_l":
                    row.update(original_threshold=row["threshold"],original_unit=row["unit"],
                        fact_code="hemoglobin_g_dl",unit="g/dL",threshold=row["threshold"]/10)
                elif row["fact_code"] in {"absolute_neutrophil_count_ul","absolute_neutrophil_count_mm3","platelet_count_ul","platelet_count_mm3"}:
                    row.update(original_threshold=row["threshold"],original_unit=row["unit"],
                        fact_code=("absolute_neutrophil_count_10_9_l" if row["fact_code"].startswith("absolute") else "platelet_count_10_9_l"),
                        unit="10^9/L",threshold=row["threshold"]/1000)
                row["criterion_id"]=f"{tid}:risk-numeric:{j+1}"
            trial={"nct_id":tid,"title":record.title,"criteria":rows,"conditions":record.conditions,
                "retrieval_rank":rank,"eligibility_text":text,"retrieved_at":envelope["retrieved_at"],
                "source_url":f"https://clinicaltrials.gov/study/{tid}","split":"heldout",
                "rejected_numeric_rows":rejected,
                "unmodeled_clause_context":"Only numeric comparison and inclusion/exclusion direction. Assay, phase/time, transfusion and other clinical qualifiers remain unmodeled; not full clinical eligibility."}
            trial["scope"]=describe_protocol_scope(trial)
            candidates.append(trial)
        candidates.sort(key=lambda t:(-len(t["criteria"]),t["retrieval_rank"]))
        selected=candidates[:4]
        if len(selected) != 4: raise ValueError(f"{name}: insufficient unused sources")
        used.update(t["nct_id"] for t in selected)
        groups.append({"group_id":name,"query":GROUPS[name],"trials":selected})
    result={"version":"risk-method-unused-protocol-subsets-v1","prior_trial_ids":sorted(prior),
        "selection":"Four richest supported unconditional scalar subsets per query; retrieval rank breaks ties; fixed before method outcome evaluation",
        "scope":"Purpose-selected numeric subsets; synthetic patients only; no whole-protocol clinical truth",
        "groups":groups}
    output.mkdir(parents=True,exist_ok=True)
    (output/"protocol_subsets.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    for g in groups:
        for t in g["trials"]:
            print(g["group_id"],t["nct_id"],len(t["criteria"]))
            for r in t["criteria"]: print(" ",r["source_text"])


if __name__ == "__main__":
    p=argparse.ArgumentParser();p.add_argument("--source",type=Path,default=Path("runs/paper-trial-sources-20261022"))
    p.add_argument("--output",type=Path,default=Path("data/paper_risk_method_v1"));a=p.parse_args()
    build(a.source,a.output)
