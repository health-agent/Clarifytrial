"""Fix an externally annotated numeric source probe before model evaluation.

Stratified, bounded subset; no claim of whole-protocol clinical ground truth.
Keep all source text and annotation formulas. Do not repair source/reference
disagreements, select by model results, or count duplicate annotations as cases.
"""
from __future__ import annotations

import argparse
from collections import Counter,defaultdict
from itertools import product
import json
from pathlib import Path
import random
import re
import shutil

from clarifytrial.preparation.leaf_numeric_reference import compile_numeric_reference


def build(source: Path, output: Path, *, exclude_cases=None, seed=20261023, followup_subset=False):
    rows=[json.loads(line) for line in (source/"annotations.jsonl").read_text(encoding="utf-8").splitlines()]
    existing=set()
    for root in (Path("configs"),Path("data")):
        for path in root.rglob("*.json"):
            existing.update(re.findall(r"NCT\d{8}",path.read_text(encoding="utf-8-sig")))
    development_ids=set()
    if exclude_cases:
        development_ids={c["nct_id"] for c in json.loads(exclude_cases.read_text(encoding="utf-8"))["cases"]}
        existing.update(development_ids)
    groups=defaultdict(list)
    for row in rows:
        if row.get("raw_text") is not None:
            groups[(row["nct_id"],row["kind"],row["raw_text"])].append(row)
    rejected=Counter(); candidates=[]
    for key, versions in sorted(groups.items()):
        # Multiple syntax references can be semantically equivalent, but an
        # arbitrary annotator must not silently become the clinical answer key.
        if len(versions)>1 and len({r.get("canonical_syntax") for r in versions})>1:
            rejected["multiple_annotation_syntaxes"]+=1;continue
        row=min(versions,key=lambda r:r["source_path"])
        if row["nct_id"] in existing:
            rejected["previously_used_protocol"]+=1;continue
        if "\ufffd" in row["raw_text"]:
            rejected["source_replacement_character"]+=1;continue
        if not row.get("numeric_filters"):
            rejected["no_numeric_filter"]+=1;continue
        try: reference=compile_numeric_reference(row["logical_form"])
        except ValueError as error:
            rejected[str(error)]+=1;continue
        facts=reference["facts"]
        stratum=("multi_fact" if len(facts)>1 else "range" if reference["ambiguous_range_count"] else
                 "age_scalar" if facts[0]["label"]=="age" else "other_scalar")
        candidates.append((stratum,row,reference,versions))
    rng=random.Random(seed)
    by_stratum=defaultdict(list)
    for item in candidates:by_stratum[item[0]].append(item)
    for items in by_stratum.values():rng.shuffle(items)
    quota=({"multi_fact":0,"range":8,"age_scalar":8,"other_scalar":8} if followup_subset else
           {"multi_fact":2,"range":16,"age_scalar":15,"other_scalar":15})
    chosen=[];used=set()
    for stratum,limit in quota.items():
        for item in by_stratum[stratum]:
            if sum(x[0]==stratum for x in chosen)>=limit:break
            if item[1]["nct_id"] in used:continue
            chosen.append(item);used.add(item[1]["nct_id"])
    cases=[]
    for stratum,row,reference,versions in chosen:
        domains={};labels={};units={}
        for fact in reference["facts"]:
            thresholds=sorted(set(fact["thresholds"]))
            values=set()
            for threshold in thresholds:
                delta=max(.01,abs(threshold)*.01)
                values.update((round(threshold-delta,6),threshold,round(threshold+delta,6)))
            for low,high in zip(thresholds,thresholds[1:]):values.add(round((low+high)/2,6))
            code=fact["code"];domains[code]=sorted(values);labels[code]=fact["label"];units[code]=fact["unit"]
        total=1
        for values in domains.values():total*=len(values)+1
        all_worlds=[dict(zip(domains, values)) for values in product(*([None,*values] for values in domains.values()))]
        if total>129:
            # Keep every single-fact boundary with all other facts missing,
            # then fill by a fixed random draw. No model output enters sampling.
            mandatory=[{code:None for code in domains}]
            mandatory.extend({c:(value if c==code else None) for c in domains} for code in domains for value in domains[code])
            remaining=[w for w in all_worlds if w not in mandatory]
            all_worlds=mandatory+rng.sample(remaining,129-len(mandatory))
        kind={"INC":"inclusion","EXC":"exclusion"}[row["kind"]]
        notes=[]
        review_notes={
            "NCT03868852":"Raw ALT and AST is represented by union in the external annotation; not clinically adjudicated or repaired.",
            "NCT03867344":"External annotation uses union across semicolon-separated lab conditions; raw text does not explicitly say OR.",
            "NCT03924843":"Raw text also requires sputum production ability; the external numeric reference does not represent that conjunct.",
            "NCT03923543":"Raw text includes a procedure-related qualifier absent from the numeric annotation.",
            "NCT03928587":"Raw text explicitly includes both bounds and refers to inclusion; generic annotated BETWEEN still retains endpoint alternatives.",
            "NCT03862625":"Raw inclusion timepoint is not represented in the numeric annotation.",
            "NCT03864133":"Raw baseline timepoint is not represented in the numeric annotation.",
            "NCT03928015":"Raw baseline/screening timepoint is not represented in the numeric annotation.",
            "NCT03861637":"Raw screening phase is not represented in the numeric annotation.",
            "NCT03925844":"Raw text refers to membership of a named study population, not represented by the numeric age annotation.",
            "NCT03865316":"Raw consent timepoint is not represented by the numeric annotation.",
            "NCT03861156":"Raw screening period is not represented by the numeric annotation.",
            "NCT03931941":"Raw screening timepoint is not represented by the numeric annotation.",
            "NCT03862027":"Raw pre-enrollment timepoint is not represented by the numeric annotation.",
            "NCT03920150":"External measured-entity label is generic serum level while raw text identifies vitamin D; strict binding is not clinical terminology validation.",
        }
        if row["nct_id"] in review_notes:notes.append(review_notes[row["nct_id"]])
        cases.append({"case_id":row["source_path"].split("/")[-1].removesuffix(".js"),
            "nct_id":row["nct_id"],"stratum":stratum,"kind":kind,"text":kind.title()+" Criteria:\n"+row["raw_text"],
            "quote":row["raw_text"],"formula":reference["formula"],"reference_alternatives":reference["reference_alternatives"],
            "reference_notes":notes,"domains":domains,"units":units,"fact_labels":labels,
            "atoms":[[row["raw_text"],code,op,value] for code,op,value in reference["atoms"]],
            "world_values":all_worlds,"exhaustive_world_count":total,
            "ambiguous_range_count":reference["ambiguous_range_count"],"reference_numeric_atom_count":reference["numeric_atom_count"],
            "reference_annotation":row["logical_form"],"source_location":row["source_url"],
            "annotation_paths":[r["source_path"] for r in versions],"full_protocol_confirmation_allowed":False})
    output.mkdir(parents=True,exist_ok=True)
    result={"status":"fixed before model comparison; annotation agreement, not clinical truth",
        "source":"UW Bio-NLP leaf-corpora, fixed revision in DATA_SOURCES.md","seed":seed,
        "excluded_development_protocols":len(development_ids),
        "prior_protocol_ids":len(existing),"unique_raw_clauses":len(groups),"exclusions":dict(rejected),
        "representable_candidates":len(candidates),"candidate_strata":{k:len(v) for k,v in by_stratum.items()},
        "quotas":quota,"selected_strata":dict(Counter(c["stratum"] for c in cases)),
        "unique_selected_protocols":len(used),"cases":cases}
    (output/"cases.json").write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")
    shutil.copy2(source/"LICENSE.upstream",output/"LICENSE.upstream")
    print(json.dumps({k:v for k,v in result.items() if k!="cases"},indent=2))
    print("worlds",sum(len(c["world_values"]) for c in cases))
    return result


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,default=Path("runs/leaf-reference-prepared-20261023"))
    parser.add_argument("--output",type=Path,default=Path("docs/internal/results/leaf-protocol-v1"))
    parser.add_argument("--exclude-cases",type=Path)
    parser.add_argument("--seed",type=int,default=20261023)
    parser.add_argument("--followup-subset",action="store_true",help="Bounded numeric follow-up after development; no population or clinical confirmation implied")
    args=parser.parse_args()
    build(args.source,args.output,exclude_cases=args.exclude_cases,seed=args.seed,followup_subset=args.followup_subset)
