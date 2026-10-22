"""Compare first-source trust, file counting, and source-aware acquisition.

All values/errors/source labels are synthetic. Reuses the fixed trial subsets and
patient generator; no clinical source independence or error rate is estimated.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import random
from statistics import mean
import time

from clarifytrial.contracts import PatientState
from clarifytrial.evidence_corroboration import EvidenceOrigin
from clarifytrial.interactive.corroboration_planner import CorroborationPlanner
from clarifytrial.interactive.corroboration_runtime import evaluate_corroborated_view
from clarifytrial.interactive.paper_benchmark import (AS_OF, QUALITY_PRIORS, RoutePlanner, evidence,
    erroneous_value_index, generate_patients, make_model, reference_statuses, runtime_statuses, runtime_view)
from clarifytrial.interactive.statistics import stratified_bootstrap_mean


POLICIES = ("trust_first", "file_quorum", "two_step_sources", "budget_sources")
SETTINGS = {"independent": (0., False), "shared_half": (.5, False),
            "shared_all": (1., False), "copied_source": (1., True)}


def csv_write(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)


def run(source, output, *, patients=48, max_groups=None, resamples=2000, error_shift=2):
    data=json.loads(source.read_text(encoding="utf-8"))
    output.mkdir(parents=True,exist_ok=True)
    rows=[]
    for group in data["groups"][:max_groups]:
        started=time.perf_counter()
        model=make_model(group,"heldout")
        view=runtime_view(group,"heldout")
        trials=[t for t in group["trials"] if t["split"]=="heldout"]
        units={r["fact_code"]:r["unit"] for t in group["trials"] for r in t["criteria"]}
        seed=21000+sum(map(ord,group["group_id"]))
        population=generate_patients(model.domains,patients,seed=seed)
        first=RoutePlanner(model,quality=QUALITY_PRIORS,compact=True)
        planner=CorroborationPlanner(model,error_probability=.05,error_shift=error_shift)
        copied=CorroborationPlanner(model,error_probability=.05,distinct_source_groups=False,error_shift=error_shift)
        for patient,truth in enumerate(population):
            rng=random.Random(seed*10000+patient)
            draws={(j,r):rng.random() for j in range(len(model.codes)) for r in range(2)}
            common_switch=[rng.random() for _ in model.codes]
            common_error=[rng.random()<.05 for _ in model.codes]
            individual={(j,r):rng.random()<.05 for j in range(len(model.codes)) for r in range(2)}
            true_values={code:model.domains[j][truth[j]] for j,code in enumerate(model.codes)}
            gold=reference_statuses(trials,true_values,set(model.codes))
            for mask in range(2):
                initial=tuple(value if j%3==mask else -1 for j,value in enumerate(truth))
                initial_status=model.statuses(initial)
                initial_facts=[];initial_origins={}
                scopes={code:f"frozen:{patient}:{mask}:{code}" for code in model.codes}
                # Every method starts with the same already-corroborated inputs.
                # As in the earlier stress study, only newly acquired values err.
                for j,code in enumerate(model.codes):
                    if initial[j]<0:continue
                    for route in range(2):
                        f=evidence(code,true_values[code],units[code],sequence=-2*j-route-1)
                        initial_facts.append(f)
                        initial_origins[f.evidence_id]=EvidenceOrigin(f"source-{route}",scopes[code])
                for setting,(rho,duplicate) in SETTINGS.items():
                    errors={(j,r):common_error[j] if common_switch[j]<rho else individual[(j,r)]
                            for j in range(len(model.codes)) for r in range(2)}
                    for visit_limit in (0,1):
                        for budget in (1,2,3):
                            for policy in POLICIES:
                                observed=initial;pending=(-1,)*len(model.codes)
                                attempted=visits=invalid=0
                                facts=initial_facts.copy();origins=initial_origins.copy()
                                reports={};trace=[]
                                for step in range(budget):
                                    if policy=="trust_first":
                                        _,action=first.solve(observed,attempted,visit_limit-visits,budget-step)
                                    else:
                                        selected=copied if duplicate and policy!="file_quorum" else planner
                                        horizon=min(2,budget-step) if policy=="two_step_sources" else budget-step
                                        _,action=selected.solve(observed,pending,attempted,visit_limit-visits,horizon)
                                    if action is None:break
                                    fact,route=action
                                    assert not attempted & (1<<(2*fact+route))
                                    attempted|=1<<(2*fact+route);visits+=route
                                    assert visits<=visit_limit
                                    valid=draws[(fact,route)]<QUALITY_PRIORS[route]
                                    outcome="valid" if valid else ("no_answer","stale","missing_date","wrong_source","unverified")[(patient+fact+route)%5]
                                    corrupted=valid and errors[(fact,route)]
                                    index=(erroneous_value_index(truth[fact],len(model.domains[fact]),shift=error_shift) if corrupted else
                                           (truth[fact] if valid else (truth[fact]+2)%len(model.domains[fact])))
                                    code=model.codes[fact]
                                    f=evidence(code,model.domains[fact][index],units[code],outcome,step+1)
                                    if f is not None:
                                        facts.append(f)
                                        family="source-0" if duplicate else f"source-{route}"
                                        origins[f.evidence_id]=EvidenceOrigin(family,scopes[code])
                                    if valid:
                                        reports[(fact,route)]=index
                                        if policy=="trust_first":
                                            observed=observed[:fact]+(index,)+observed[fact+1:]
                                        elif pending[fact]<0:
                                            pending=pending[:fact]+(index,)+pending[fact+1:]
                                        else:
                                            agreement=pending[fact]==index
                                            if agreement and (not duplicate or policy=="file_quorum"):
                                                observed=observed[:fact]+(index,)+observed[fact+1:]
                                            pending=pending[:fact]+(-1,)+pending[fact+1:]
                                    else:invalid+=1
                                    trace.append({"fact":code,"route":route,"outcome":outcome,
                                        "reported_index":index if f is not None else None,"content_error":bool(corrupted)})
                                statuses=model.statuses(observed)
                                accepted={code:model.domains[j][observed[j]] for j,code in enumerate(model.codes) if observed[j]>=0}
                                assert statuses==reference_statuses(trials,accepted,set(accepted))
                                if policy=="trust_first":
                                    actual=runtime_statuses(view,facts);questions=[]
                                else:
                                    used_origins=({eid:EvidenceOrigin("document:"+eid,origin.comparison_scope) for eid,origin in origins.items()}
                                                  if policy=="file_quorum" else origins)
                                    runtime=evaluate_corroborated_view(view,PatientState(patient_id="synthetic",as_of=AS_OF,
                                        facts=[f for f in facts if f is not None]),origins=used_origins,scope_by_concept=scopes)
                                    actual=tuple({"confirmed":1,"ineligible":0}.get(d.confirmation_status.value,-1) for d in runtime.decisions)
                                    questions=runtime.pending_evidence_requests
                                if actual!=statuses:raise AssertionError((group["group_id"],patient,policy,trace,statuses,actual))
                                wrong_confirmed=sum(a==1 and b==0 for a,b in zip(statuses,gold))
                                wrong_excluded=sum(a==0 and b==1 for a,b in zip(statuses,gold))
                                opportunity=1 not in initial_status and 1 in gold
                                rows.append({"group":group["group_id"],"patient":patient,"mask":mask,"setting":setting,
                                    "visit_limit":visit_limit,"budget":budget,"policy":policy,"trials":len(gold),
                                    "correct":sum(a==b for a,b in zip(statuses,gold)),"confirmed":sum(a==b==1 for a,b in zip(statuses,gold)),
                                    "excluded":sum(a==b==0 for a,b in zip(statuses,gold)),"eligible_total":gold.count(1),"ineligible_total":gold.count(0),
                                    "wrong_confirmed":wrong_confirmed,"wrong_excluded":wrong_excluded,"unsafe":wrong_confirmed+wrong_excluded,
                                    "unresolved":statuses.count(-1),"initial_correct":sum(a==b for a,b in zip(initial_status,gold)),
                                    "actions":len(trace),"visits":visits,"invalid":invalid,"questions":len(questions),
                                    "first_opportunity":int(opportunity),"first_confirmed":int(opportunity and any(a==b==1 for a,b in zip(statuses,gold))),
                                    "trace":json.dumps(trace)})
            if (patient+1)%8==0:print(group["group_id"],patient+1,"patients",round(time.perf_counter()-started,1),"s",flush=True)
        first.solve.cache_clear();planner.solve.cache_clear();copied.solve.cache_clear()
        csv_write(output/"patient_results.csv",rows)
        print(group["group_id"],"complete",len(rows),"replays",round(time.perf_counter()-started,1),"s",flush=True)
    totals=defaultdict(list)
    for row in rows:totals[tuple(row[k] for k in ("setting","visit_limit","budget","policy"))].append(row)
    metrics=[]
    fields=("trials","correct","confirmed","excluded","eligible_total","ineligible_total","wrong_confirmed","wrong_excluded",
            "unsafe","unresolved","initial_correct","actions","visits","invalid","questions","first_opportunity","first_confirmed")
    for key,subset in totals.items():
        row=dict(zip(("setting","visit_limit","budget","policy"),key));row.update({f:sum(r[f] for r in subset) for f in fields})
        row["replays"]=len(subset);row["recovery"]=row["correct"]/row["trials"];metrics.append(row)
    csv_write(output/"policy_metrics.csv",metrics)
    comparisons=[]
    for setting in SETTINGS:
        for visit in (0,1):
            pairs=defaultdict(dict)
            for row in rows:
                if row["setting"]==setting and row["visit_limit"]==visit and row["budget"]==3:
                    pairs[(row["group"],row["patient"],row["mask"])][row["policy"]]=row
            for comparator in ("trust_first","two_step_sources","file_quorum"):
                for metric in ("correct","unsafe","visits","actions"):
                    clustered=defaultdict(list)
                    for (g,p,m),pair in pairs.items():
                        a,b=pair["budget_sources"],pair[comparator]
                        scale=a["trials"] if metric in {"correct","unsafe"} else 1
                        clustered[(g,p)].append((a[metric]-b[metric])/scale)
                    strata=defaultdict(list)
                    for (g,p),values in clustered.items():strata[g].append(mean(values))
                    comparisons.append({"setting":setting,"visit_limit":visit,"comparator":comparator,"metric":metric,
                        "effect":stratified_bootstrap_mean(strata,cluster_unit="synthetic_patient_within_fixed_protocol_stratum",resamples=resamples)})
    summary={"assessment_scope":"same selected protocol subsets; no clinical validation","full_protocol_confirmation_allowed":False,
        "patients":patients*len(data["groups"][:max_groups]),"replays":len(rows),"source_version":data["version"],
        "error_probability":.05,"error_channel":f"nonidentity cyclic shift {error_shift}, falling back to one if identity","quality_priors":QUALITY_PRIORS,
        "model":"Independent source errors and development feature marginals; maximizes corroborated decision count, not guaranteed correctness.",
        "limits":"Initial evidence is already correctly corroborated. Synthetic exact frozen-value agreement and caller-declared source/scope identities; shared errors remain undetectable.",
        "settings":SETTINGS,"metrics":metrics,"comparisons":comparisons}
    (output/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    return summary


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,default=Path("data/paper_source_benchmark_v2/protocol_subsets.json"))
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--patients",type=int,default=48)
    parser.add_argument("--max-groups",type=int)
    parser.add_argument("--resamples",type=int,default=2000)
    parser.add_argument("--error-shift",type=int,default=2)
    args=parser.parse_args()
    run(args.source,args.output,patients=args.patients,max_groups=args.max_groups,resamples=args.resamples,error_shift=args.error_shift)
