"""Per-protocol effects and crossed patient/protocol resampling from saved traces.

Reconstruct only the already executed observations; never rerun or tune selection.
The source corpus is purposively selected, so the crossed range is a sensitivity
on these arrays, not a population-level clinical generalization guarantee.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
from statistics import mean

from clarifytrial.interactive.paper_benchmark import generate_patients,make_model,reference_statuses
from clarifytrial.interactive.statistics import crossed_stratified_bootstrap_mean,stratified_bootstrap_mean


def write_csv(path,rows):
    with path.open("w",encoding="utf-8-sig",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def analyze(source: Path, run: Path, output: Path, *, setting="imperfect", resamples=5000,target="quality_budget"):
    data=json.loads(source.read_text(encoding="utf-8"))
    summary=json.loads((run/"summary.json").read_text(encoding="utf-8"))
    if summary["source_version"]!=data["version"]:
        raise ValueError("trace source version mismatch")
    output.mkdir(parents=True,exist_ok=True)
    split=summary["split"]
    count=summary["patients_per_group"]
    groups=data["groups"][:summary["groups"]]
    prepared={}
    for group in groups:
        model=make_model(group,split)
        trials=[t for t in group["trials"] if t["split"]==split]
        seed=sum(map(ord,group["group_id"]))+(21000 if split=="heldout" else 11000)
        population=generate_patients(model.domains,count,seed=seed,shifted=setting=="value_shift")
        prepared[group["group_id"]]=(model,trials,population)
    cells={}
    totals=defaultdict(lambda:defaultdict(int))
    patient_outcomes=defaultdict(lambda:defaultdict(int))
    for row in csv.DictReader((run/"patient_results.csv").open(encoding="utf-8-sig")):
        if row["setting"]!=setting or row["visit_limit"]!="1" or row["budget"]!="3":
            continue
        group=row["group"]
        patient,mask=int(row["patient"]),int(row["mask"])
        model,trials,population=prepared[group]
        truth=population[patient]
        values={code:model.domains[j][truth[j]] for j,code in enumerate(model.codes)}
        gold=reference_statuses(trials,values,set(values))
        observed=list(v if j%3==mask else -1 for j,v in enumerate(truth))
        initial_values={code:model.domains[j][observed[j]] for j,code in enumerate(model.codes) if observed[j]>=0}
        initial=reference_statuses(trials,initial_values,set(initial_values))
        for action in json.loads(row["trace"]):
            fact=model.codes.index(action["fact"])
            outcome=action["outcome"]
            if outcome=="valid":
                observed[fact]=truth[fact]
            elif outcome=="verified_value_error":
                observed[fact]=(truth[fact]+2)%len(model.domains[fact])
            elif outcome not in {"no_answer","stale","missing_date","wrong_source","unverified"}:
                raise ValueError(f"unknown saved outcome {outcome}")
        accepted={code:model.domains[j][observed[j]] for j,code in enumerate(model.codes) if observed[j]>=0}
        status=reference_statuses(trials,accepted,set(accepted))
        correct=tuple(int(a==b) for a,b in zip(status,gold))
        unsafe=sum(a!=-1 and a!=b for a,b in zip(status,gold))
        if sum(correct)!=int(row["correct"]) or unsafe!=int(row["unsafe"]):
            raise ValueError("trace reconstruction disagrees with recorded outcomes")
        cells[(group,patient,mask,row["policy"])]=correct
        patient_metrics=patient_outcomes[row["policy"]]
        patient_metrics["settings"]+=1
        patient_metrics["with_eligible_subset"]+=1 in gold
        patient_metrics["found_true_eligible_subset"]+=any(a==b==1 for a,b in zip(status,gold))
        patient_metrics["false_eligible_confirmation"]+=any(a==1 and b!=1 for a,b in zip(status,gold))
        opportunity=1 in gold and 1 not in initial
        patient_metrics["new_confirmation_opportunities"]+=opportunity
        patient_metrics["found_after_no_initial_confirmation"]+=opportunity and any(a==b==1 for a,b in zip(status,gold))
        patient_metrics["visits"]+=int(row["visits"])
        patient_metrics["actions"]+=int(row["actions"])
        for j,trial in enumerate(trials):
            key=(group,trial["nct_id"],row["policy"])
            totals[key]["observations"]+=1
            totals[key]["correct"]+=correct[j]
            totals[key]["eligible_total"]+=gold[j]==1
            totals[key]["ineligible_total"]+=gold[j]==0
            totals[key]["confirmed"]+=gold[j]==status[j]==1
            totals[key]["excluded"]+=gold[j]==status[j]==0
    protocol_rows=[]
    for (group,tid,policy),metrics in sorted(totals.items()):
        trial=next(t for t in prepared[group][1] if t["nct_id"]==tid)
        protocol_rows.append({"group":group,"trial_id":tid,"policy":policy,
            "scalar_leaves":len(trial["criteria"]),"unique_facts":len({r["fact_code"] for r in trial["criteria"]}),
            "source_declarations":len(trial.get("source_declarations",[])),**metrics})
    if not protocol_rows:
        raise ValueError("no matching replay rows")
    write_csv(output/"protocol_metrics.csv",protocol_rows)
    comparisons=[]
    for baseline in ("coverage","decision_myopic","decision_budget","quality_myopic"):
        matrices={}
        trial_effects=[]
        for group in groups:
            gid=group["group_id"]
            trials=prepared[gid][1]
            matrix=[[mean(cells[(gid,p,m,target)][j]-cells[(gid,p,m,baseline)][j] for m in (0,1))
                     for j in range(len(trials))] for p in range(count)]
            matrices[gid]=matrix
            for j,trial in enumerate(trials):
                trial_effects.append({"group":gid,"trial_id":trial["nct_id"],"difference":mean(row[j] for row in matrix)})
        result=crossed_stratified_bootstrap_mean(matrices,resamples=resamples)
        patient_result=stratified_bootstrap_mean({g:[mean(row) for row in matrix] for g,matrix in matrices.items()},
            cluster_unit="patient_averaged_over_masks_and_protocols",resamples=resamples)
        overall=result["mean_difference"]
        n=len(trial_effects)
        leave_one_out=[(overall*n-t["difference"])/(n-1) for t in trial_effects]
        comparisons.append({"baseline":baseline,"crossed_resampling":result,"patient_resampling":patient_result,"trial_effects":trial_effects,
            "leave_one_protocol_out_range":{"lower":min(leave_one_out),"upper":max(leave_one_out)},
            "protocol_wins":sum(t["difference"]>0 for t in trial_effects),
            "protocol_ties":sum(t["difference"]==0 for t in trial_effects),
            "protocol_losses":sum(t["difference"]<0 for t in trial_effects)})
    payload={"source_version":data["version"],"setting":setting,"visit_limit":1,"budget":3,"target_policy":target,
        "scope":"Post hoc per-protocol and crossed resampling sensitivity; same expanded protocols, no new clinical cohort.",
        "patient_goal_metrics":dict(patient_outcomes),"comparisons":comparisons}
    (output/"protocol_sensitivity.json").write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    return payload


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,default=Path("data/paper_source_benchmark_v2/protocol_subsets.json"))
    parser.add_argument("--run",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--setting",default="imperfect")
    parser.add_argument("--resamples",type=int,default=5000)
    parser.add_argument("--target",default="quality_budget")
    args=parser.parse_args()
    analyze(args.source,args.run,args.output,setting=args.setting,resamples=args.resamples,target=args.target)
