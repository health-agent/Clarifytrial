"""Development -> independent calibration -> frozen evaluation, no LLM calls.

One mask and one source-noise setting per independent synthetic patient episode.
The target risk is any wrong NEW subset decision conditional on any NEW output.
LTT selection uses calibration only. Test/stress never choose a cutoff.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import random
import time

from clarifytrial.interactive.direction_corroboration import SourcePairModel, SourcePairPlanner
from clarifytrial.interactive.paper_benchmark import (RoutePlanner, generate_patients, make_model, reference_statuses)
from clarifytrial.interactive.risk_aware_acquisition import RiskAwareAcquisition
from clarifytrial.interactive.selective_calibration import CalibrationCandidate, calibrate, clopper_pearson_upper
from clarifytrial.disclaimer import DEFAULT_MEDICAL_DISCLAIMER


METHODS=("legacy_first","legacy_direction","risk_myopic","risk_entropy","risk_bayes","risk_independent","risk_targeted")
CUTOFFS=(.01,.03,.10)
SEEDS={"development":34001,"calibration":45001,"test":56001,"stress":67001}


def definitions(cutoffs=CUTOFFS):
    return [{"name":method if method.startswith("legacy") else f"{method}:{cutoff:g}","method":method,"cutoff":cutoff}
        for method in METHODS for cutoff in ((None,) if method.startswith("legacy") else cutoffs)]


def write_csv(path, rows):
    if not rows:return
    with path.open("w",encoding="utf-8-sig",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def simulate(model, gold, fixed, outcomes, planner, method):
    state=(-2,)*(2*len(fixed));observed=fixed;pending=(-1,)*len(fixed)
    visits=0;attempted=0;trace=[]
    for step in range(3):
        remaining=3-step
        if method == "legacy_first":
            _,action=planner.solve(observed,attempted,1-visits,remaining)
        elif method == "legacy_direction":
            _,action=planner.solve(observed,pending,attempted,1-visits,remaining)
        else:
            policy={"risk_entropy":"entropy","risk_bayes":"bayes_error"}.get(method,"targeted")
            action=planner.choose(fixed,state,1-visits,remaining,policy=policy,
                horizon=1 if method=="risk_myopic" else 2)
        if action is None:break
        fact,route=action
        if fixed[fact]>=0 or attempted & (1 << (2*fact+route)) or route>1-visits:
            raise AssertionError("invalid/repeated/forbidden acquisition")
        value=outcomes[fact,route];visits+=route;attempted|=1 << (2*fact+route)
        state=state[:2*fact+route]+(value,)+state[2*fact+route+1:]
        trace.append([fact,route,value])
        if value<0:continue
        if method == "legacy_first":
            # RoutePlanner compact states use a representative, but the actual
            # observed value is supplied here; alias before each solve below.
            observed=observed[:fact]+(value,)+observed[fact+1:]
        elif method == "legacy_direction":
            if pending[fact]<0:pending=pending[:fact]+(value,)+pending[fact+1:]
            else:
                pair=planner.model.encode(fact,pending[fact],value)
                observed=observed[:fact]+(pair,)+observed[fact+1:]
                pending=pending[:fact]+(-1,)+pending[fact+1:]
    if method.startswith("legacy"):
        labels=(model.statuses(observed) if method=="legacy_first" else planner.model.statuses(observed))
        risks=[]
    else:
        decisions=planner.decisions(fixed,state);labels=tuple(d.label for d in decisions)
        risks=[d.model_error_upper for d in decisions]
    initial=model.statuses(fixed)
    new=[i for i in range(len(gold)) if initial[i]<0 and labels[i]>=0]
    wrong=[i for i in new if labels[i]!=gold[i]]
    correct=sum(a==b for a,b in zip(labels,gold))
    opportunity=1 not in initial and 1 in gold
    return {"trials":len(gold),"correct":correct,"new_correct":sum(labels[i]==gold[i] for i in new),
        "new_released":len(new),"selected":int(bool(new)),"any_wrong":int(bool(wrong)),
        "wrong_confirmed":sum(labels[i]==1 for i in wrong),"wrong_excluded":sum(labels[i]==0 for i in wrong),
        "unresolved":labels.count(-1),"actions":len(trace),"visits":visits,
        "initial_correct":sum(a==b for a,b in zip(initial,gold)),"first_opportunity":int(opportunity),
        "first_confirmed":int(opportunity and any(a==b==1 for a,b in zip(labels,gold))),
        "trace":json.dumps(trace),"labels":json.dumps(labels),"model_risks":json.dumps(risks)}


def run(source, output, *, phase, episodes, split="heldout", methods=None, selection=None,
        independent_belief=False, seed_override=None, cutoffs=CUTOFFS):
    data=json.loads(source.read_text(encoding="utf-8"));groups=data["groups"]
    output.mkdir(parents=True,exist_ok=True)
    configs=definitions(cutoffs)
    if methods:configs=[c for c in configs if c["method"] in methods]
    family_size=len(configs)
    if selection:
        record=json.loads(selection.read_text(encoding="utf-8"))
        if record["phase"]!="calibration" or record["source_protocol_ids"]!=sorted(t["nct_id"] for g in groups for t in g["trials"] if t["split"]==split):
            raise ValueError("frozen calibration must match deployment protocols")
        if record.get("independent_belief",False)!=independent_belief:
            raise ValueError("deployment belief model differs from frozen calibration")
        family_size=record.get("family_size",17)
        chosen={r["chosen"]["name"] for r in record["calibration_by_method"].values() if r["chosen"]}
        configs=[c for c in configs if c["name"] in chosen]
    elif phase in {"test","stress"}:
        raise ValueError("test requires selection frozen from calibration")
    # Draw group, mask, sharing and latent patient independently per episode.
    # This defines the IID mixture required by the episode-level calibration.
    seed=SEEDS[phase] if seed_override is None else seed_override
    rng=random.Random(seed);private=[]
    models=[make_model(g,split) for g in groups]
    for i in range(episodes):
        group=rng.randrange(len(groups));m=models[group];mask=rng.randrange(2);rho=rng.choice((0.,.5,1.))
        truth=generate_patients(m.domains,1,seed=rng.randrange(2**50),shifted=phase=="stress")[0]
        fixed=tuple(v if j%3==mask else -1 for j,v in enumerate(truth))
        noise=random.Random(seed*100000+i);p=.15 if phase=="stress" else .05
        outcomes={}
        for fact,domain in enumerate(m.domains):
            shared=noise.random()<rho;common_error=noise.random()<p
            common_wrong=noise.choice([v for v in range(len(domain)) if v!=truth[fact]])
            for route,q in enumerate((.70,.95)):
                sufficient=noise.random()<q
                error=common_error if shared else noise.random()<p
                wrong=common_wrong if shared else noise.choice([v for v in range(len(domain)) if v!=truth[fact]])
                outcomes[fact,route]=(-1 if not sufficient else wrong if error else truth[fact])
        trials=[t for t in groups[group]["trials"] if t["split"]==split]
        gold=reference_statuses(trials,{c:m.domains[j][truth[j]] for j,c in enumerate(m.codes)},set(m.codes))
        if gold!=m.statuses(truth):raise AssertionError("independent numeric reference mismatch")
        private.append((group,mask,rho,truth,fixed,outcomes,gold))
    rows=[];started=time.perf_counter()
    for config in configs:
        method=config["method"];begin=time.perf_counter()
        for group,m in enumerate(models):
            if method=="legacy_first":planner=RoutePlanner(m,quality=(.70,.95),compact=False)
            elif method=="legacy_direction":planner=SourcePairPlanner(SourcePairModel(m),error_probability=.05)
            else:planner=RiskAwareAcquisition(m,risk_cutoff=config["cutoff"],
                coupling=(0.,) if independent_belief or method=="risk_independent" else (0.,.5,1.))
            for i,(g,mask,rho,truth,fixed,outcomes,gold) in enumerate(private):
                if g!=group:continue
                row=simulate(m,gold,fixed,outcomes,planner,method)
                patient_id=f"{phase}:{i}" if seed_override is None else f"{phase}:{seed}:{i}"
                row={"patient":patient_id,"group":groups[g]["group_id"],"mask":mask,"coupling":rho,
                    "configuration":config["name"],"method":method,**row}
                rows.append(row)
            if isinstance(planner,RiskAwareAcquisition):planner.clear_caches()
            else:planner.solve.cache_clear()
        write_csv(output/"patient_results.csv",rows)
        print(phase,config["name"],len(private),"episodes",round(time.perf_counter()-begin,2),"seconds",flush=True)
    metrics=[]
    fields=("trials","correct","new_correct","new_released","selected","any_wrong","wrong_confirmed","wrong_excluded",
            "unresolved","actions","visits","initial_correct","first_opportunity","first_confirmed")
    for config in configs:
        subset=[r for r in rows if r["configuration"]==config["name"]]
        item={**config,**{f:sum(r[f] for r in subset) for f in fields}}
        item["episodes"]=len(subset);item["recovery"]=item["correct"]/item["trials"]
        item["selective_episode_risk"]=item["any_wrong"]/item["selected"] if item["selected"] else None
        item["selective_episode_risk_upper_95"]=clopper_pearson_upper(item["any_wrong"],item["selected"])
        metrics.append(item)
    calibration_by_method={}
    if phase=="calibration":
        for method in METHODS:
            candidates=[CalibrationCandidate(c["name"],c["selected"],c["any_wrong"],c["new_correct"],c["visits"])
                for c in metrics if c["method"]==method]
            if candidates:calibration_by_method[method]=calibrate(candidates,family_size=family_size)
    write_csv(output/"policy_metrics.csv",metrics)
    summary={"phase":phase,"seed":seed,"episodes":episodes,"scope":"Selected numeric subsets, synthetic IID episodes; not clinical enrollment",
        "source_protocol_ids":sorted(t["nct_id"] for g in groups for t in g["trials"] if t["split"]==split),
        "risk_target":f"P(any wrong new subset decision | any new decision released), alpha=.05 delta=.05; family={family_size}",
        "independent_belief":independent_belief,"family_size":family_size,
        "calibration_assumption":"Fixed policies, fully labeled IID episodes from the same mixture; not arbitrary distribution shift",
        "configurations":configs,"metrics":metrics,"calibration_by_method":calibration_by_method,
        "elapsed_seconds":time.perf_counter()-started,"medical_disclaimer":DEFAULT_MEDICAL_DISCLAIMER}
    (output/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    print(json.dumps(metrics,indent=2),flush=True)


if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("--source",type=Path,default=Path("data/paper_risk_method_v1/protocol_subsets.json"))
    p.add_argument("--output",type=Path,required=True);p.add_argument("--phase",choices=SEEDS,required=True)
    p.add_argument("--episodes",type=int,default=1200);p.add_argument("--split",default="heldout")
    p.add_argument("--methods",nargs="+");p.add_argument("--selection",type=Path)
    p.add_argument("--independent-belief",action="store_true")
    p.add_argument("--seed",type=int);p.add_argument("--cutoffs",type=float,nargs="+",default=CUTOFFS);a=p.parse_args()
    run(a.source,a.output,phase=a.phase,episodes=a.episodes,split=a.split,methods=a.methods,selection=a.selection,
        independent_belief=a.independent_belief,seed_override=a.seed,cutoffs=tuple(a.cutoffs))
