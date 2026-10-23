"""Paired episode analysis after calibration-fixed method selection.

Primary comparator: the same-model risk_myopic selected as strongest by
calibration new-correct count, before reading test outcomes. Other comparisons
are secondary. Protocol subsets remain fixed; no clinical population inference.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
from statistics import mean

from clarifytrial.interactive.statistics import stratified_bootstrap_mean


def analyze(root, output, *, resamples=5000):
    output.mkdir(parents=True,exist_ok=True)
    result={"primary_comparator":"risk_myopic", "selection":"strongest same-belief baseline by calibration, not test",
        "uncertainty":"Paired synthetic patient-episode bootstrap, conditional on fixed purpose-selected numeric subsets and mixture",
        "phases":{}}
    for phase in ("test","stress"):
        if not (root/phase/"summary.json").exists():continue
        summary=json.loads((root/phase/"summary.json").read_text(encoding="utf-8"))
        with (root/phase/"patient_results.csv").open(encoding="utf-8-sig",newline="") as stream:
            rows=list(csv.DictReader(stream))
        for r in rows:
            for k in ("trials","correct","new_correct","selected","any_wrong","visits","first_opportunity","first_confirmed"):
                r[k]=int(r[k])
        pairs=defaultdict(dict)
        for row in rows:pairs[row["patient"]][row["method"]]=row
        metrics=summary["metrics"]
        methods={r["method"] for r in rows}
        comparisons=[]
        for comparator in sorted(methods-{"risk_targeted"}):
            for metric in ("correct","visits","first_confirmed"):
                strata=defaultdict(list)
                for pair in pairs.values():
                    a,b=pair["risk_targeted"],pair[comparator]
                    denominator=a["trials"] if metric=="correct" else 1
                    strata[a["group"]].append((a[metric]-b[metric])/denominator)
                comparisons.append({"comparator":comparator,"metric":metric,
                    **stratified_bootstrap_mean(strata,cluster_unit="independent_patient_episode_one_mask",resamples=resamples)})
        grouped=[]
        for group in sorted({r["group"] for r in rows}):
            for method in sorted(methods):
                subset=[r for r in rows if r["group"]==group and r["method"]==method]
                grouped.append({"group":group,"method":method,"episodes":len(subset),
                    **{k:sum(r[k] for r in subset) for k in ("trials","correct","selected","any_wrong","visits","first_opportunity","first_confirmed")}})
        coupling=[]
        for rho in ("0.0","0.5","1.0"):
            for method in sorted(methods):
                subset=[r for r in rows if r["coupling"]==rho and r["method"]==method]
                coupling.append({"coupling":rho,"method":method,"episodes":len(subset),
                    **{k:sum(r[k] for r in subset) for k in ("correct","selected","any_wrong","visits")}})
        result["phases"][phase]={"metrics":metrics,"paired_comparisons":comparisons,"group_metrics":grouped,
            "coupling_metrics":coupling,"calibration_guarantee_applies":phase=="test"}
    (output/"analysis.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    for phase,s in result["phases"].items():
        for row in s["metrics"]:print(phase,row["name"],row["correct"],row["visits"],row["any_wrong"],row["selected"],flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--root",type=Path,default=Path("."));p.add_argument("--output",type=Path,required=True)
    p.add_argument("--resamples",type=int,default=5000);a=p.parse_args();analyze(a.root,a.output,resamples=a.resamples)
