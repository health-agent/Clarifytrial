"""Compare exact-value and criterion-direction agreement on fixed synthetic data.

Exploratory follow-up on the existing 24 heldout protocol subsets. New uniform
wrong-value channels allow two erroneous sources to report different wrong values.
No independent clinical cohort or source error calibration is implied.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
from pathlib import Path
import random
from statistics import mean
import time

from clarifytrial.contracts import PatientState
from clarifytrial.evidence_corroboration import EvidenceOrigin
from clarifytrial.interactive.corroboration_runtime import evaluate_corroborated_view
from clarifytrial.interactive.direction_corroboration import SourcePairModel, SourcePairPlanner
from clarifytrial.interactive.paper_benchmark import (AS_OF, QUALITY_PRIORS, RoutePlanner, evidence,
    erroneous_value_index, generate_patients, make_model, reference_statuses, runtime_statuses, runtime_view)
from clarifytrial.interactive.statistics import stratified_bootstrap_mean, crossed_stratified_bootstrap_mean
from run_corroboration_research import csv_write


POLICIES=("trust_first", "exact_sources", "direction_same_trace", "direction_two_step", "direction_sources")


def reference_pairs(trials, pairs, mode):
    """Direct threshold/tree reference, independent of planner and runtime guards."""
    result=[]
    for trial in trials:
        leaves={}
        for row in trial["criteria"]:
            reports=pairs.get(row["fact_code"], ())
            if len(reports) != 2 or (mode == "exact_value" and reports[0] != reports[1]):
                leaves[row["criterion_id"]]=-1
                continue
            outcomes=[]
            for value in reports:
                t, op=row["threshold"], row["operator"]
                met={"gte": value >= t, "gt": value > t, "lte": value <= t, "lt": value < t, "eq": value == t}[op]
                outcomes.append(int(met if row["kind"] == "inclusion" else not met))
            leaves[row["criterion_id"]]=outcomes[0] if outcomes[0] == outcomes[1] else -1

        def tree(node):
            if node["operator"] == "criterion":
                return leaves[node["criterion_id"]]
            values=[tree(child) for child in node["children"]]
            if node["operator"] == "all":
                return 0 if 0 in values else -1 if -1 in values else 1
            if node["operator"] == "any":
                return 1 if 1 in values else -1 if -1 in values else 0
            raise ValueError("unsupported reference tree")

        values=list(leaves.values())
        result.append(tree(trial["eligibility_logic"]) if trial.get("eligibility_logic") else
                      0 if 0 in values else -1 if -1 in values else 1)
    return tuple(result)


def run(source, output, *, patients=48, max_groups=None, resamples=5000,
        error_probabilities=(.05, .15), channel="uniform_wrong"):
    data=json.loads(source.read_text(encoding="utf-8"))
    output.mkdir(parents=True, exist_ok=True)
    rows=[]
    for group in data["groups"][:max_groups]:
        started=time.perf_counter()
        model=make_model(group, "heldout")
        models={mode: SourcePairModel(model, mode=mode) for mode in ("exact_value", "criterion_direction")}
        view=runtime_view(group, "heldout")
        trials=[t for t in group["trials"] if t["split"] == "heldout"]
        units={r["fact_code"]: r["unit"] for t in group["trials"] for r in t["criteria"]}
        seed=21000+sum(map(ord, group["group_id"]))
        population=generate_patients(model.domains, patients, seed=seed)
        first=RoutePlanner(model, quality=QUALITY_PRIORS, compact=True)
        for probability in error_probabilities:
            planners={mode: SourcePairPlanner(m, error_probability=probability, channel=channel) for mode, m in models.items()}
            for patient, truth in enumerate(population):
                rng=random.Random(seed*10000+patient)
                draws={(j,r): rng.random() for j in range(len(model.codes)) for r in range(2)}
                switches=[rng.random() for _ in model.codes]
                common_errors=[rng.random() for _ in model.codes]
                individual_errors={(j,r): rng.random() for j in range(len(model.codes)) for r in range(2)}
                common_wrong=[rng.choice([i for i in range(len(d)) if i != truth[j]]) for j,d in enumerate(model.domains)]
                individual_wrong={(j,r): rng.choice([i for i in range(len(d)) if i != truth[j]])
                                  for j,d in enumerate(model.domains) for r in range(2)}
                true_values={code: model.domains[j][truth[j]] for j,code in enumerate(model.codes)}
                gold=reference_statuses(trials, true_values, set(model.codes))
                for mask in range(2):
                    initial=tuple(v if j%3 == mask else -1 for j,v in enumerate(truth))
                    initial_status=model.statuses(initial)
                    scopes={code: f"frozen:{patient}:{mask}:{code}" for code in model.codes}
                    initial_facts=[]; initial_origins={}; initial_reports={}
                    for j,code in enumerate(model.codes):
                        if initial[j] < 0: continue
                        for route in range(2):
                            f=evidence(code, true_values[code], units[code], sequence=-2*j-route-1)
                            initial_facts.append(f)
                            initial_origins[f.evidence_id]=EvidenceOrigin(f"source-{route}", scopes[code])
                            initial_reports[(j,route)]=truth[j]
                    for rho in (0., 1.):
                        setting=f"{'independent' if rho == 0 else 'shared_all'}_p{round(probability*100):02d}"
                        potential={}
                        for j in range(len(model.codes)):
                            shared=switches[j] < rho
                            for route in range(2):
                                corrupt=(common_errors[j] if shared else individual_errors[(j,route)]) < probability
                                wrong=(common_wrong[j] if shared else individual_wrong[(j,route)])
                                if channel == "cyclic": wrong=erroneous_value_index(truth[j], len(model.domains[j]))
                                potential[(j,route)]=wrong if corrupt else truth[j]
                        exact_trace=None
                        for policy in POLICIES:
                            observed=initial; pending=(-1,)*len(model.codes)
                            attempted=visits=invalid=0
                            facts=initial_facts.copy(); origins=initial_origins.copy(); reports=initial_reports.copy(); trace=[]
                            planning_mode="exact_value" if policy in {"exact_sources", "direction_same_trace"} else "criterion_direction"
                            output_mode="exact_value" if policy == "exact_sources" else "criterion_direction"
                            for step in range(3):
                                if policy == "trust_first":
                                    _, action=first.solve(observed, attempted, 1-visits, 3-step)
                                else:
                                    horizon=min(2, 3-step) if policy == "direction_two_step" else 3-step
                                    _, action=planners[planning_mode].solve(observed, pending, attempted, 1-visits, horizon)
                                if action is None: break
                                fact_index, route=action
                                assert not attempted & (1 << (2*fact_index+route))
                                attempted |= 1 << (2*fact_index+route); visits += route
                                assert visits <= 1
                                valid=draws[action] < QUALITY_PRIORS[route]
                                outcome="valid" if valid else ("no_answer", "stale", "missing_date", "wrong_source", "unverified")[(patient+fact_index+route)%5]
                                index=potential[action] if valid else (truth[fact_index]+2)%len(model.domains[fact_index])
                                code=model.codes[fact_index]
                                f=evidence(code, model.domains[fact_index][index], units[code], outcome, step+1)
                                if f is not None:
                                    facts.append(f); origins[f.evidence_id]=EvidenceOrigin(f"source-{route}", scopes[code])
                                if valid:
                                    reports[action]=index
                                    if policy == "trust_first":
                                        observed=observed[:fact_index]+(index,)+observed[fact_index+1:]
                                    elif pending[fact_index] < 0:
                                        pending=pending[:fact_index]+(index,)+pending[fact_index+1:]
                                    else:
                                        pair=models[planning_mode].encode(fact_index, pending[fact_index], index)
                                        observed=observed[:fact_index]+(pair,)+observed[fact_index+1:]
                                        pending=pending[:fact_index]+(-1,)+pending[fact_index+1:]
                                else: invalid += 1
                                trace.append({"fact": code, "route": route, "outcome": outcome,
                                    "reported_index": index if f is not None else None, "content_error": valid and index != truth[fact_index]})
                            if policy == "exact_sources": exact_trace=trace
                            if policy == "direction_same_trace": assert trace == exact_trace
                            if policy == "trust_first":
                                statuses=model.statuses(observed)
                                accepted={code: model.domains[j][v] for j,(code,v) in enumerate(zip(model.codes, observed)) if v >= 0}
                                assert statuses == reference_statuses(trials, accepted, set(accepted))
                                actual=runtime_statuses(view, facts)
                            else:
                                pairs={code: tuple(model.domains[j][reports[(j,r)]] for r in range(2))
                                       for j,code in enumerate(model.codes) if all((j,r) in reports for r in range(2))}
                                encoded=tuple(models[output_mode].encode(j, reports[(j,0)], reports[(j,1)])
                                    if (j,0) in reports and (j,1) in reports else -1 for j in range(len(model.codes)))
                                statuses=models[output_mode].statuses(encoded)
                                assert statuses == reference_pairs(trials, pairs, output_mode)
                                runtime=evaluate_corroborated_view(view, PatientState(patient_id="synthetic", as_of=AS_OF, facts=facts),
                                    origins=origins, scope_by_concept=scopes, agreement_mode=output_mode)
                                actual=tuple({"confirmed":1, "ineligible":0}.get(d.confirmation_status.value, -1) for d in runtime.decisions)
                            if actual != statuses: raise AssertionError((group["group_id"], patient, policy, trace, statuses, actual))
                            wrong_confirmed=sum(a == 1 and b == 0 for a,b in zip(statuses, gold))
                            wrong_excluded=sum(a == 0 and b == 1 for a,b in zip(statuses, gold))
                            opportunity=1 not in initial_status and 1 in gold
                            rows.append({"group":group["group_id"], "patient":patient, "mask":mask, "setting":setting, "policy":policy,
                                "trials":len(gold), "correct":sum(a == b for a,b in zip(statuses, gold)),
                                "confirmed":sum(a == b == 1 for a,b in zip(statuses,gold)), "excluded":sum(a == b == 0 for a,b in zip(statuses,gold)),
                                "wrong_confirmed":wrong_confirmed, "wrong_excluded":wrong_excluded, "unsafe":wrong_confirmed+wrong_excluded,
                                "unresolved":statuses.count(-1), "initial_correct":sum(a == b for a,b in zip(initial_status,gold)),
                                "actions":len(trace), "visits":visits, "invalid":invalid,
                                "first_opportunity":int(opportunity), "first_confirmed":int(opportunity and any(a == b == 1 for a,b in zip(statuses,gold))),
                                "gold":json.dumps(gold), "statuses":json.dumps(statuses), "trace":json.dumps(trace)})
                if (patient+1)%8 == 0: print(group["group_id"], probability, patient+1, round(time.perf_counter()-started,1), "s", flush=True)
            for planner in planners.values(): planner.solve.cache_clear()
        first.solve.cache_clear()
        csv_write(output/"patient_results.csv", rows)
        print(group["group_id"], "complete", len(rows), "replays", flush=True)
    totals=defaultdict(list)
    for row in rows: totals[(row["setting"], row["policy"])].append(row)
    fields=("trials", "correct", "confirmed", "excluded", "wrong_confirmed", "wrong_excluded", "unsafe", "unresolved", "initial_correct",
            "actions", "visits", "invalid", "first_opportunity", "first_confirmed")
    metrics=[]
    for (setting, policy), subset in totals.items():
        row={"setting":setting, "policy":policy, **{f:sum(r[f] for r in subset) for f in fields}}
        row["recovery"]=row["correct"]/row["trials"]
        row["new_decisions"]=row["correct"]+row["unsafe"]-row["initial_correct"]
        row["wrong_among_new"]=row["unsafe"]/row["new_decisions"] if row["new_decisions"] else None
        metrics.append(row)
    csv_write(output/"policy_metrics.csv", metrics)
    comparisons=[]
    for setting in sorted({r["setting"] for r in rows}):
        paired=defaultdict(dict)
        for row in rows:
            if row["setting"] == setting: paired[(row["group"], row["patient"], row["mask"])][row["policy"]]=row
        for a,b in (("direction_sources", "exact_sources"), ("direction_same_trace", "exact_sources"),
                    ("direction_sources", "direction_same_trace"), ("direction_sources", "direction_two_step"),
                    ("direction_sources", "trust_first")):
            results={"setting":setting, "candidate":a, "comparator":b}
            for metric in ("correct", "unsafe", "visits", "actions"):
                clustered=defaultdict(list); crossed=defaultdict(lambda:defaultdict(list))
                for (g,p,m), pair in paired.items():
                    scale=pair[a]["trials"] if metric in {"correct", "unsafe"} else 1
                    clustered[(g,p)].append((pair[a][metric]-pair[b][metric])/scale)
                    if metric in {"correct", "unsafe"}:
                        sa, sb, gold=(json.loads(pair[policy][field]) for policy,field in ((a,"statuses"),(b,"statuses"),(a,"gold")))
                        for j,(x,y,t) in enumerate(zip(sa,sb,gold)):
                            delta=int(x == t)-int(y == t) if metric == "correct" else int(x >= 0 and x != t)-int(y >= 0 and y != t)
                            crossed[g][(p,j)].append(delta)
                strata=defaultdict(list)
                for (g,p), values in clustered.items(): strata[g].append(mean(values))
                results[metric]=stratified_bootstrap_mean(strata, cluster_unit="synthetic_patient_within_fixed_protocol_stratum", resamples=resamples)
                if crossed:
                    matrices={g:[[mean(values[(p,j)]) for j in range(1+max(j for p,j in values))]
                                  for p in range(1+max(p for p,j in values))] for g,values in crossed.items()}
                    results[metric+"_crossed_sensitivity"]=crossed_stratified_bootstrap_mean(matrices,resamples=resamples)
            comparisons.append(results)
    summary={"source_version":data["version"], "assessment_scope":"exploratory reuse of selected numeric protocol subsets",
        "full_protocol_confirmation_allowed":False, "channel":channel, "error_probabilities":error_probabilities,
        "patients":patients*len(data["groups"][:max_groups]), "replays":len(rows), "budget":3, "visit_limit":1,
        "quality_priors":QUALITY_PRIORS, "initial_evidence":"Correct paired reports; only new returns may err",
        "planner":"Independent per-source content errors and development marginals; source error correlation is misspecification",
        "metrics":metrics, "comparisons":comparisons}
    (output/"summary.json").write_text(json.dumps(summary,indent=2),encoding="utf-8")
    return summary


if __name__ == "__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,default=Path("data/paper_source_benchmark_v2/protocol_subsets.json"))
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--patients",type=int,default=48)
    parser.add_argument("--max-groups",type=int)
    parser.add_argument("--resamples",type=int,default=5000)
    parser.add_argument("--error-probabilities",type=float,nargs="+",default=[.05,.15])
    parser.add_argument("--channel",choices=["uniform_wrong","cyclic"],default="uniform_wrong")
    a=parser.parse_args()
    run(a.source,a.output,patients=a.patients,max_groups=a.max_groups,resamples=a.resamples,
        error_probabilities=a.error_probabilities,channel=a.channel)
