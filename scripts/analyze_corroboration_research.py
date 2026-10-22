"""Post-run source-quorum resource bounds, examples, and retained summaries."""
from __future__ import annotations

import argparse
from collections import defaultdict
import csv
import json
from pathlib import Path
import random
import shutil

from clarifytrial.interactive.paper_benchmark import (QUALITY_PRIORS, erroneous_value_index,
    generate_patients, make_model, reference_statuses)
from run_corroboration_research import SETTINGS, csv_write


def analyze(source, run, output, *, sensitivity=None, corrected=None, error_shift=2):
    output.mkdir(parents=True,exist_ok=True)
    data=json.loads(source.read_text(encoding="utf-8"))
    summary=json.loads((run/"summary.json").read_text(encoding="utf-8"))
    risk_rows=[]
    for row in summary["metrics"]:
        resolved=row["correct"]+row["unsafe"]
        newly_resolved=resolved-row["initial_correct"]
        risk_rows.append({**{k:row[k] for k in ("setting","visit_limit","budget","policy")},
            "resolved":resolved,"coverage":resolved/row["trials"],"wrong":row["unsafe"],
            "wrong_among_resolved":row["unsafe"]/resolved if resolved else None,
            "newly_resolved":newly_resolved,
            "wrong_among_newly_resolved":row["unsafe"]/newly_resolved if newly_resolved else None})
    csv_write(output/"risk_coverage.csv",risk_rows)
    patient_count=summary["patients"]//len(data["groups"])
    rows=[]
    with (run/"patient_results.csv").open(encoding="utf-8-sig") as stream:
        for row in csv.DictReader(stream):
            if row["budget"]=="3" and row["visit_limit"]=="1":rows.append(row)
    cohorts={}
    bounds={}
    for group in data["groups"]:
        model=make_model(group,"heldout")
        trials=[t for t in group["trials"] if t["split"]=="heldout"]
        seed=21000+sum(map(ord,group["group_id"]))
        population=generate_patients(model.domains,patient_count,seed=seed)
        for patient,truth in enumerate(population):
            cohorts[(group["group_id"],patient)]=(model,truth)
            rng=random.Random(seed*10000+patient)
            draws={(j,r):rng.random() for j in range(len(model.codes)) for r in range(2)}
            switches=[rng.random() for _ in model.codes]
            common=[rng.random()<.05 for _ in model.codes]
            individual={(j,r):rng.random()<.05 for j in range(len(model.codes)) for r in range(2)}
            truth_values={code:model.domains[j][truth[j]] for j,code in enumerate(model.codes)}
            gold=reference_statuses(trials,truth_values,set(model.codes))
            for mask in range(2):
                initial=tuple(index if j%3==mask else -1 for j,index in enumerate(truth))
                initial_status=model.statuses(initial)
                base=sum(a==b for a,b in zip(initial_status,gold))
                for setting,(rho,duplicate) in SETTINGS.items():
                    for standard in ("sources","files"):
                        best=base;best_fact=None;first=False
                        if not duplicate or standard=="files":
                            for fact in range(len(model.codes)):
                                if initial[fact]>=0 or any(draws[(fact,r)]>=QUALITY_PRIORS[r] for r in range(2)):continue
                                errors=[common[fact] if switches[fact]<rho else individual[(fact,r)] for r in range(2)]
                                reports=[erroneous_value_index(truth[fact],len(model.domains[fact]),shift=error_shift)
                                         if error else truth[fact] for error in errors]
                                if reports[0]!=reports[1]:continue
                                observed=initial[:fact]+(reports[0],)+initial[fact+1:]
                                statuses=model.statuses(observed)
                                accepted={code:model.domains[j][value] for j,(code,value) in enumerate(zip(model.codes,observed)) if value>=0}
                                assert statuses==reference_statuses(trials,accepted,set(accepted))
                                score=sum(a==b for a,b in zip(statuses,gold))
                                if score>best:best,best_fact=score,model.codes[fact]
                                first=first or (1 not in initial_status and any(a==b==1 for a,b in zip(statuses,gold)))
                        bounds[(group["group_id"],patient,mask,setting,standard)]=(best,best_fact,first)
    totals=defaultdict(lambda:defaultdict(int))
    lookup={}
    for row in rows:
        g,p,m=row["group"],int(row["patient"]),int(row["mask"])
        lookup[(g,p,m,row["setting"],row["policy"])]=row
        if row["policy"]=="trust_first":continue
        standard="files" if row["policy"]=="file_quorum" else "sources"
        cap,_,first_cap=bounds[(g,p,m,row["setting"],standard)]
        assert cap>=int(row["correct"])
        target=totals[(row["setting"],row["policy"])]
        for field in ("correct","trials","initial_correct","first_confirmed","first_opportunity"):
            target[field]+=int(row[field])
        target["oracle_correct"]+=cap
        target["oracle_first_confirmed"]+=int(first_cap)
        target["gap_to_oracle"]+=cap-int(row["correct"])
    aggregate=[{"setting":setting,"policy":policy,**counts} for (setting,policy),counts in totals.items()]
    csv_write(output/"source_budget_bounds.csv",aggregate)
    examples=[]
    criteria=(
        ("shared_errors_pass_source_comparison",lambda r:r["setting"]=="shared_all" and r["policy"]=="budget_sources" and int(r["unsafe"])>0),
        ("copied_records_pass_file_counting",lambda r:r["setting"]=="copied_source" and r["policy"]=="file_quorum" and int(r["unsafe"])>0),
    )
    for name,condition in criteria:
        chosen=next((row for row in rows if condition(row)),None)
        if chosen is None:continue
        g,p,m,setting=chosen["group"],int(chosen["patient"]),int(chosen["mask"]),chosen["setting"]
        model,truth=cohorts[(g,p)]
        paired=[]
        for policy in ("trust_first","file_quorum","two_step_sources","budget_sources"):
            row=lookup[(g,p,m,setting,policy)]
            trace=json.loads(row["trace"])
            for step in trace:
                j=model.codes.index(step["fact"])
                step["synthetic_true_value"]=model.domains[j][truth[j]]
                step["reported_value"]=(model.domains[j][step["reported_index"]] if step["reported_index"] is not None else None)
            paired.append({"policy":policy,**{k:int(row[k]) for k in ("correct","unsafe","wrong_confirmed","wrong_excluded","actions","visits")},"trace":trace})
        examples.append({"example":name,"scope":"Post-hoc synthetic illustration, not clinical validation",
            "group":g,"patient":p,"mask":m,"setting":setting,"policies":paired})
    (output/"examples.json").write_text(json.dumps(examples,indent=2),encoding="utf-8")
    analytic=[]
    for p in (.01,.05,.15):
        for rho in (0.,.5,1.):
            both_wrong=rho*p+(1-rho)*p*p
            agree=1-2*(1-rho)*p*(1-p)
            analytic.append({"source_error_probability":p,"shared_component":rho,"agreement_probability":agree,
                "wrong_given_agreement":both_wrong/agree})
    (output/"agreement_identity.json").write_text(json.dumps(analytic,indent=2),encoding="utf-8")
    for name in ("summary.json","policy_metrics.csv"):shutil.copy2(run/name,output/name)
    if sensitivity:
        for name in ("summary.json","policy_metrics.csv"):shutil.copy2(sensitivity/name,output/("shift1_"+name))
    if corrected:
        for name in ("summary.json","policy_metrics.csv"):shutil.copy2(corrected/name,output/("corrected_error_"+name))
    print(json.dumps(aggregate,indent=2))


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,default=Path("data/paper_source_benchmark_v2/protocol_subsets.json"))
    parser.add_argument("--run",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--sensitivity",type=Path)
    parser.add_argument("--corrected",type=Path)
    parser.add_argument("--error-shift",type=int,default=2)
    a=parser.parse_args()
    analyze(a.source,a.run,a.output,sensitivity=a.sensitivity,corrected=a.corrected,error_shift=a.error_shift)
