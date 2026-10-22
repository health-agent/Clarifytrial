"""Summarize external annotation agreement, guarding against clinical claims.

Protocol clauses, not their generated worlds, are the resampling unit. The
complex strata include reference/text differences flagged before inference.
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
import csv
import json
from pathlib import Path
from statistics import mean

from clarifytrial.interactive.statistics import stratified_bootstrap_mean


def write_csv(path, rows):
    with path.open("w",encoding="utf-8-sig",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def analyze(cases_path, output, methods, *, resamples=5000, posthoc_notes=None, candidate="v8"):
    cases=json.loads(cases_path.read_text(encoding="utf-8"))["cases"]
    expected_ids={c["case_id"] for c in cases}
    posthoc=(json.loads(posthoc_notes.read_text(encoding="utf-8"))["issues"] if posthoc_notes else [])
    posthoc_ids={item["case_id"] for item in posthoc}
    results={}
    for name,path in methods.items():
        result=json.loads(path.read_text(encoding="utf-8"))
        rows={r["case_id"]:r for r in result["cases"]}
        if set(rows)!=expected_ids:raise ValueError("model run is not the complete fixed cohort: "+name)
        results[name]=(result,rows)
    totals=[];case_rows=[]
    for name,(run,rows) in results.items():
        for case in cases:
            row=rows[case["case_id"]]
            if row["source_text"]!=case["text"] or row["reference_formula"]!=case["formula"]:
                raise ValueError("source or reference changed")
            if row.get("reference_alternatives")!=case.get("reference_alternatives"):
                raise ValueError("reference endpoint conventions changed")
            worlds=row["worlds"];scored=[w for w in worlds if w["expected"] is not None]
            resolved=[w for w in scored if w["expected"]!="not_confirmed"]
            ambiguous=[w for w in worlds if w["expected"] is None]
            case_rows.append({"method":name,"case_id":case["case_id"],"nct_id":case["nct_id"],"stratum":case["stratum"],
                "preflagged_reference":bool(case["reference_notes"]),"scored":len(scored),
                "agreement":sum(w["observed"]==w["expected"] for w in scored),
                "case_agreement":mean(w["observed"]==w["expected"] for w in scored),
                "reference_resolvable":len(resolved),
                "resolved_agreement":sum(w["observed"]==w["expected"] for w in resolved),
                "abstained_resolvable":sum(w["observed"] not in {"confirmed","ineligible","preparation_failed"} for w in resolved),
                "discordant_confirmation":sum(w["observed"]=="confirmed" and w["expected"]=="ineligible" for w in scored),
                "discordant_exclusion":sum(w["observed"]=="ineligible" and w["expected"]=="confirmed" for w in scored),
                "decisive_reference_unknown":sum(w["expected"]=="not_confirmed" and w["observed"] in {"confirmed","ineligible"} for w in scored),
                "ambiguous_reference":len(ambiguous),"decisive_ambiguous_reference":sum(w["observed"] in {"confirmed","ineligible"} for w in ambiguous),
                "failed":bool(row["error"]),"guard_unsupported":row.get("supported") is False,
                "numeric_count":row.get("numeric_count",0),"all_scored_agree":all(w["observed"]==w["expected"] for w in scored)})
        method_rows=[r for r in case_rows if r["method"]==name]
        total={"method":name,"clauses":len(method_rows),"macro_clause_agreement":mean(r["case_agreement"] for r in method_rows)}
        for field in ("scored","agreement","reference_resolvable","resolved_agreement","abstained_resolvable","discordant_confirmation",
            "discordant_exclusion","decisive_reference_unknown","ambiguous_reference","decisive_ambiguous_reference","failed",
            "guard_unsupported","all_scored_agree"):
            total[field]=sum(r[field] for r in method_rows)
        total["micro_world_agreement"]=total["agreement"]/total["scored"]
        total["provider_total_tokens"]=run["summary"]["provider_total_tokens"]
        total["effective_models"]=run["summary"]["effective_models"]
        totals.append(total)
    write_csv(output/"case_metrics.csv",case_rows)
    # The always-abstain row uses exactly the same fixed synthetic worlds.
    exemplar=next(iter(results.values()))[1]
    abstain_cases=[]
    for case in cases:
        worlds=[w for w in exemplar[case["case_id"]]["worlds"] if w["expected"] is not None]
        abstain_cases.append(mean(w["expected"]=="not_confirmed" for w in worlds))
    comparisons=[]
    if {"old",candidate} <= set(results):
        for subset in ("all_clauses","without_preflagged_references","without_all_documented_reference_issues"):
            strata=defaultdict(list)
            for case in cases:
                if subset!="all_clauses" and case["reference_notes"]:continue
                if subset=="without_all_documented_reference_issues" and case["case_id"] in posthoc_ids:continue
                a=next(r for r in case_rows if r["method"]==candidate and r["case_id"]==case["case_id"])
                b=next(r for r in case_rows if r["method"]=="old" and r["case_id"]==case["case_id"])
                strata[case["stratum"]].append(a["case_agreement"]-b["case_agreement"])
            comparisons.append({"subset":subset,f"{candidate}_minus_old_macro_agreement":stratified_bootstrap_mean(strata,
                cluster_unit="source_clause_unique_protocol_within_fixed_selection_stratum",resamples=resamples)})
    strata_rows=[]
    for method in results:
        for stratum in sorted({c["stratum"] for c in cases}):
            rows=[r for r in case_rows if r["method"]==method and r["stratum"]==stratum]
            strata_rows.append({"method":method,"stratum":stratum,"clauses":len(rows),
                "macro_clause_agreement":mean(r["case_agreement"] for r in rows),
                **{k:sum(r[k] for r in rows) for k in ("scored","agreement","reference_resolvable","resolved_agreement",
                    "abstained_resolvable","discordant_confirmation","discordant_exclusion","decisive_reference_unknown")}})
    write_csv(output/"stratum_metrics.csv",strata_rows)
    summary={"scope":"External numeric-annotation agreement on a bounded stratified subset; not clinical truth or full protocol eligibility",
        "resampling":"One fixed source clause per protocol; synthetic boundary/missing worlds are not independent patients",
        "clauses":len(cases),"preflagged_reference_clauses":sum(bool(c["reference_notes"]) for c in cases),
        "always_abstain_macro_agreement":mean(abstain_cases),"metrics":totals,"comparisons":comparisons,
        "posthoc_reference_issues":posthoc,
        "all_calls_single_generation":"One generation per method and clause; model stochastic variation is unmeasured"}
    (output/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(totals,indent=2))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases",type=Path,default=Path("docs/internal/results/leaf-protocol-v1/cases.json"))
    parser.add_argument("--output",type=Path,default=Path("docs/internal/results/leaf-protocol-v1"))
    parser.add_argument("--method",action="append",required=True,help="NAME=path/to/results.json")
    parser.add_argument("--resamples",type=int,default=5000)
    parser.add_argument("--posthoc-notes",type=Path)
    parser.add_argument("--candidate",default="v8")
    args=parser.parse_args()
    methods={name:Path(path) for name,path in (arg.split("=",1) for arg in args.method)}
    analyze(args.cases,args.output,methods,resamples=args.resamples,posthoc_notes=args.posthoc_notes,candidate=args.candidate)
