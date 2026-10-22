"""Run disjoint protocol/patient evaluation; no external LLM or real patients."""
from __future__ import annotations

import argparse
import csv
import json
import random
from collections import defaultdict
from pathlib import Path
from statistics import mean

from clarifytrial.interactive.paper_benchmark import (
    POLICIES, QUALITY_PRIORS, RoutePlanner, evidence, generate_patients, make_model,
    reference_statuses, runtime_statuses, runtime_view, erroneous_value_index,
)
from clarifytrial.interactive.statistics import stratified_bootstrap_mean


def csv_write(path, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def run(source, output, *, patients=48, split="heldout", bootstrap=2000, max_groups=None, penalties=(), stress=False, settings_override=None, compact=False):
    if patients < 1 or bootstrap < 1 or (max_groups is not None and not 1 <= max_groups <= 6):
        raise ValueError("positive patients/bootstrap and one to six groups are required")
    if any(p < 0 for p in penalties) or len(set(penalties)) != len(penalties):
        raise ValueError("visit penalties must be unique and nonnegative")
    data = json.loads(source.read_text(encoding="utf-8"))
    rows = []
    settings=("verified_value_error","value_dependent_failure") if stress else ("ideal","imperfect","value_shift","quality_shift")
    if settings_override:
        if not set(settings_override)<={"ideal","imperfect","value_shift","quality_shift","verified_value_error","value_dependent_failure"}:
            raise ValueError("unknown evaluation setting")
        settings=tuple(settings_override)
    output.mkdir(parents=True, exist_ok=True)
    for group in data["groups"][:max_groups]:
        model = make_model(group, split)
        trials = [t for t in group["trials"] if t["split"] == split]
        view = runtime_view(group, split)
        units = {r["fact_code"]:r["unit"] for t in group["trials"] for r in t["criteria"]}
        seed = sum(map(ord, group["group_id"])) + (21000 if split == "heldout" else 11000)
        # Plans cached across repeated patients, never across different models.
        for setting in settings:
            planners = {True: RoutePlanner(model, quality=(1.,1.) if setting == "ideal" else QUALITY_PRIORS,compact=compact),
                        False: RoutePlanner(model, quality=(1.,1.),compact=compact)}
            for penalty in penalties:
                planners[f"quality_penalty_{penalty:g}"] = RoutePlanner(model,
                    quality=(1.,1.) if setting=="ideal" else QUALITY_PRIORS, visit_penalty=penalty,compact=compact)
            actual_q = (1.,1.) if setting == "ideal" else ((.50,.85) if setting == "quality_shift" else QUALITY_PRIORS)
            population = generate_patients(model.domains, patients, seed=seed, shifted=setting == "value_shift")
            mean_rank=[mean(row[j]/(len(domain)-1) for row in population) for j,domain in enumerate(model.domains)]
            for patient_index, truth in enumerate(population):
                values = {code:model.domains[j][truth[j]] for j,code in enumerate(model.codes)}
                gold = reference_statuses(trials, values, set(model.codes))
                # Common potential route outcomes across methods, with no truth
                # available to the selector. Route success is independent of value.
                rng = random.Random(seed * 10000 + patient_index)
                draws = {(j,r):rng.random() for j in range(len(model.codes)) for r in range(2)}
                corruption = {(j,r):rng.random() for j in range(len(model.codes)) for r in range(2)}
                for mask in range(2):
                    initial = tuple(v if j % 3 == mask else -1 for j,v in enumerate(truth))
                    initial_status = model.statuses(initial)
                    initial_facts = [evidence(code, values[code], units[code], sequence=-j-1)
                                     for j,code in enumerate(model.codes) if initial[j] >= 0]
                    # Cross-check separate gold authoring, planner state, and the
                    # actual runtime before interpreting a policy score.
                    usable = {code for j,code in enumerate(model.codes) if initial[j] >= 0}
                    assert initial_status == reference_statuses(trials, values, usable)
                    assert initial_status == runtime_statuses(view, initial_facts)
                    for visit_limit in (0,1):
                        for budget in (1,2,3):
                            for policy in (*POLICIES, *(f"quality_penalty_{p:g}" for p in penalties)):
                                aware = policy.startswith("quality")
                                planner = planners.get(policy,planners[aware])
                                observed = initial
                                attempted = visits = invalid = 0
                                facts = initial_facts.copy()
                                attempts = []
                                for step in range(budget):
                                    if policy == "coverage":
                                        action = planner.coverage(observed, attempted, visit_limit-visits)
                                    else:
                                        horizon = 1 if policy.endswith("myopic") else budget-step
                                        _, action = planner.solve(observed, attempted, visit_limit-visits, horizon)
                                    if action is None:
                                        break
                                    fact,route = action
                                    assert observed[fact] < 0
                                    assert not attempted & (1 << (2*fact+route))
                                    attempted |= 1 << (2*fact+route)
                                    visits += route
                                    assert visits <= visit_limit
                                    q=actual_q[route]
                                    if setting=="value_dependent_failure":
                                        # Center within this fixed synthetic population
                                        # to preserve each fact/route's marginal q.
                                        q -= (.4 if route==0 else .05)*(truth[fact]/(len(model.domains[fact])-1)-mean_rank[fact])
                                        assert 0 <= q <= 1
                                    success = draws[action] < q
                                    outcome = "valid" if success else ("no_answer","stale","missing_date","wrong_source","unverified")[(patient_index+fact+route)%5]
                                    corrupted=success and setting=="verified_value_error" and corruption[action]<.05
                                    returned_index=(erroneous_value_index(truth[fact],len(model.domains[fact])) if corrupted else
                                        ((truth[fact]+2)%len(model.domains[fact]) if not success else truth[fact]))
                                    if success:
                                        observed = observed[:fact]+(returned_index,)+observed[fact+1:]
                                    else:
                                        invalid += 1
                                    code = model.codes[fact]
                                    # Insufficient returns can contain an obsolete
                                    # value; the guard must not use it as current truth.
                                    returned_value = model.domains[fact][returned_index]
                                    facts.append(evidence(code, returned_value, units[code], outcome, step+1))
                                    attempts.append({"fact":code,"route":route,"outcome":"verified_value_error" if corrupted else outcome})
                                statuses = model.statuses(observed)
                                usable = {code for j,code in enumerate(model.codes) if observed[j]>=0}
                                accepted_values={code:model.domains[j][observed[j]] for j,code in enumerate(model.codes) if observed[j]>=0}
                                assert statuses == reference_statuses(trials,accepted_values,usable)
                                # Actual evidence processing is independently checked
                                # for every replay, including invalid answers.
                                runtime = runtime_statuses(view, facts)
                                if runtime != statuses:
                                    raise AssertionError((group["group_id"],patient_index,policy,attempts,statuses,runtime))
                                unguarded_values = dict(values)
                                unguarded_usable = {code for j,code in enumerate(model.codes) if initial[j]>=0}
                                for f in facts:
                                    if f is not None:
                                        unguarded_values[f.concept] = f.value
                                        unguarded_usable.add(f.concept)
                                unguarded = reference_statuses(trials,unguarded_values,unguarded_usable)
                                rows.append({"group":group["group_id"],"patient":patient_index,"mask":mask,"setting":setting,
                                    "visit_limit":visit_limit,"budget":budget,"policy":policy,"trials":len(gold),
                                    "correct":sum(a==b for a,b in zip(statuses,gold)),
                                    "confirmed":sum(a==b==1 for a,b in zip(statuses,gold)),
                                    "excluded":sum(a==b==0 for a,b in zip(statuses,gold)),
                                    "eligible_total":gold.count(1),"ineligible_total":gold.count(0),
                                    "initial_correct":sum(a==b for a,b in zip(initial_status,gold)),
                                    "initial_confirmed":sum(a==b==1 for a,b in zip(initial_status,gold)),
                                    "initial_excluded":sum(a==b==0 for a,b in zip(initial_status,gold)),
                                    "unsafe":sum(a!=-1 and a!=b for a,b in zip(statuses,gold)),
                                    "value_only_unsafe":sum(a!=-1 and a!=b for a,b in zip(unguarded,gold)),
                                    "actions":len(attempts),"visits":visits,"invalid":invalid,
                                    "trace":json.dumps(attempts,ensure_ascii=False)})
            print(f"{group['group_id']} {setting}: {patients} synthetic patients; {len(rows)} replays",flush=True)
            for planner in planners.values():
                planner.solve.cache_clear()
    csv_write(output/"patient_results.csv", rows)
    grouped = defaultdict(list)
    for row in rows:
        grouped[tuple(row[k] for k in ("setting","visit_limit","budget","policy"))].append(row)
    aggregates = []
    for key, subset in grouped.items():
        totals={k:sum(r[k] for r in subset) for k in ("trials","correct","confirmed","excluded","eligible_total","ineligible_total",
            "initial_correct","initial_confirmed","initial_excluded","unsafe","value_only_unsafe","actions","visits","invalid")}
        aggregates.append(dict(zip(("setting","visit_limit","budget","policy"),key)),)
        aggregates[-1].update(totals, replays=len(subset), recovery=totals["correct"]/totals["trials"])
    csv_write(output/"policy_metrics.csv",aggregates)
    comparisons=[]
    for setting in settings:
        for visits in (0,1):
            for comparator in ("coverage","decision_myopic","decision_budget","quality_myopic"):
                selected=[r for r in rows if r["setting"]==setting and r["visit_limit"]==visits and r["budget"]==3]
                paired=defaultdict(dict)
                for r in selected:
                    paired[(r["group"],r["patient"],r["mask"])][r["policy"]]=r["correct"]/r["trials"]
                clusters=defaultdict(list)
                for (g,p,m), d in paired.items():
                    clusters[(g,p)].append(d["quality_budget"]-d[comparator])
                strata=defaultdict(list)
                for (g,p),differences in clusters.items():
                    strata[g].append(mean(differences))
                ci=stratified_bootstrap_mean(strata,cluster_unit="synthetic_patient_within_query_stratum",resamples=bootstrap)
                comparisons.append({"setting":setting,"visit_limit":visits,"comparator":comparator,"effect":ci})
    summary={"scope":data["scope"],"split":split,"patients_per_group":patients,"groups":len(data["groups"][:max_groups]),
        "base_synthetic_patients":patients*len(data["groups"][:max_groups]),
        "value_shift_variants":patients*len(data["groups"][:max_groups]) if "value_shift" in settings else 0,"replay_count":len(rows),
        "trial_count":sum(t["split"]==split for g in data["groups"][:max_groups] for t in g["trials"]),
        "quality_priors":QUALITY_PRIORS,"visit_penalties":penalties,"settings":settings,"compact_planning":compact,
        "source_version":data["version"],"relation_to_v1":data.get("relation_to_v1"),
        "verified_error_channel":"shift_two_with_binary_flip_v2; earlier saved runs used shift by two, which left binary values unchanged",
        "model":"Independent empirical feature marginals with Laplace smoothing; the planner does not model value-dependent missingness, informative failures, or factual errors in verified returns.",
        "uncertainty":"Patient bootstrap conditional on these fixed protocol subsets and synthetic generators; not a CI for new protocols or clinical deployment. Exploratory multiple comparisons, no confirmatory p-value claim.",
        "metrics":aggregates,"comparisons":comparisons}
    (output/"summary.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    return summary


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,default=Path("data/paper_source_benchmark_v1/protocol_subsets.json"))
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--patients",type=int,default=48)
    parser.add_argument("--split",choices=("development","heldout"),default="heldout")
    parser.add_argument("--bootstrap",type=int,default=2000)
    parser.add_argument("--max-groups",type=int)
    parser.add_argument("--visit-penalties",type=float,nargs="*",default=[])
    parser.add_argument("--stress",action="store_true",help="Run verified-value errors and value-dependent failures separately from the original four settings")
    parser.add_argument("--settings",nargs="*")
    parser.add_argument("--compact-planning",action="store_true")
    args=parser.parse_args()
    run(args.source,args.output,patients=args.patients,split=args.split,bootstrap=args.bootstrap,max_groups=args.max_groups,penalties=args.visit_penalties,stress=args.stress,settings_override=args.settings,compact=args.compact_planning)
