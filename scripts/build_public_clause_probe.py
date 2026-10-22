"""Select one complexity-first source clause per previously evaluated trial.

Exploratory reuse of author-selected numeric interpretations, not independent
clinical labels. Full source wording is retained; unmodeled qualifiers remain.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from itertools import product
import json
import math
from pathlib import Path
import random


# Explicit labels fixed before inference. Unit and measurement context remain
# separate; home/office BP are intentionally not merged with ordinary BP.
LABELS = {
    "absolute_neutrophil_count_10_9_l": "absolute_neutrophil_count",
    "alkaline_phosphatase_xuln": "alkaline_phosphatase", "alt_xuln": "alt", "ast_xuln": "ast",
    "bilirubin_mg_dl": "bilirubin", "bilirubin_xuln": "bilirubin",
    "body_mass_index_kg_m2": "body_mass_index", "body_weight_kg": "body_weight",
    "creatinine_clearance_ml_min": "creatinine_clearance", "dialysis": "dialysis",
    "diastolic_bp_mmhg": "diastolic_blood_pressure", "ecog_score": "ecog",
    "egfr_ml_min": "egfr", "egfr_ml_min_1_73m2": "egfr", "gilbert_syndrome": "gilbert_syndrome",
    "hba1c": "hba1c", "heart_rate_bpm": "heart_rate", "hemoglobin_g_dl": "hemoglobin",
    "home_systolic_bp_mmhg": "home_systolic_blood_pressure", "inr_ratio": "inr",
    "liver_metastases": "liver_metastases", "lvef_percent": "lvef", "ntprobnp_pg_ml": "ntprobnp",
    "office_diastolic_bp_mmhg": "office_diastolic_blood_pressure",
    "office_systolic_bp_mmhg": "office_systolic_blood_pressure", "platelet_count_10_9_l": "platelet_count",
    "serum_albumin_g_dl": "serum_albumin", "serum_creatinine_mg_dl": "serum_creatinine",
    "systolic_bp_mmhg": "systolic_blood_pressure", "uacr_mg_g": "uacr",
}


def project(node, selected, rules):
    if node["operator"] == "criterion":
        cid = node["criterion_id"]
        if cid not in selected:
            return None
        row = rules[cid]
        predicate = [row["operator"], row["fact_code"], row["threshold"]]
        return predicate if row["kind"] == "inclusion" else ["not", predicate]
    children = [project(child, selected, rules) for child in node["children"]]
    children = [child for child in children if child is not None]
    if not children:
        return None
    if len(children) == 1:
        return children[0]
    return [{"all": "and", "any": "or"}[node["operator"]], *children]


def references(node):
    if node["operator"] == "criterion":
        return {node["criterion_id"]}
    return set().union(*(references(child) for child in node["children"]))


def exact_subtree(node, selected):
    if references(node) == selected:
        return node
    for child in node.get("children", []):
        found = exact_subtree(child, selected)
        if found is not None:
            return found
    return None


def build(source, cap=129):
    cases = []
    for group in source["groups"]:
        for trial in group["trials"]:
            if trial["split"] != "heldout":
                continue
            clauses = defaultdict(list)
            for row in trial["criteria"]:
                clauses[(row["kind"], row["source_text"])].append(row)
            # Source complexity decides inclusion before seeing any model output.
            (kind, quote), selected = min(clauses.items(), key=lambda item:
                (-len(item[1]), min(row["line_number"] for row in item[1]), item[0][1]))
            rules = {row["criterion_id"]: row for row in trial["criteria"]}
            selected_ids = {r["criterion_id"] for r in selected}
            subtree = exact_subtree(trial["eligibility_logic"], selected_ids)
            if subtree is None:
                raise ValueError("selected source clause does not have an independent complete reference subtree")
            formula = project(subtree, selected_ids, rules)
            if kind == "exclusion":
                formula = ["not", formula]  # expected() negates membership once.
            domains, units = {}, {}
            for code in dict.fromkeys(row["fact_code"] for row in selected):
                relevant = [r for r in selected if r["fact_code"] == code]
                unit = relevant[0]["unit"]
                if any(r["unit"] != unit for r in relevant):
                    raise ValueError("one fact has incompatible units")
                units[code] = unit
                values = {0., 1.} if unit == "bool" else {
                    round(r["threshold"] + delta, 6) for r in relevant
                    for delta in (-max(abs(r["threshold"])*.01, .1), 0, max(abs(r["threshold"])*.01, .1))}
                domains[code] = sorted(values)
            full = list(product(*([None, *values] for values in domains.values())))
            indices = list(range(len(full)))
            if len(indices) > cap:
                rng = random.Random(20261023 + int(trial["nct_id"][3:]))
                indices = sorted({0, len(full)-1, *rng.sample(range(1, len(full)-1), cap-2)})
            cases.append({"case_id": trial["nct_id"], "group_id": group["group_id"], "kind": kind,
                "text": kind.title() + " Criteria:\n" + quote, "quote": quote,
                "title": trial["title"], "source_location": selected[0]["source_location"],
                "retrieved_at": trial["retrieved_at"], "source_lines": sorted({r["line_number"] for r in selected}),
                "formula": formula, "domains": domains, "units": units,
                "fact_labels": {code: LABELS[code] for code in domains},
                "atoms": [(r["source_text"], r["fact_code"], r["operator"], r["threshold"]) for r in selected],
                "reference_leaf_count": len(selected), "exhaustive_world_count": math.prod(len(v)+1 for v in domains.values()),
                "world_values": [dict(zip(domains, full[i])) for i in indices],
                "assessment_scope": "selected_source_clause_numeric_interpretation", "full_protocol_confirmation_allowed": False,
                "scope": "Author-selected numeric interpretation only; full source qualifiers retained, not clinical truth."})
    return cases


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("data/paper_source_benchmark_v2/protocol_subsets.json"))
    parser.add_argument("--output", type=Path, default=Path("data/paper_source_benchmark_v2/public_clause_probe_v1.json"))
    args = parser.parse_args()
    cases = build(json.loads(args.source.read_text(encoding="utf-8")))
    args.output.write_text(json.dumps({"selection": "one clause per reused heldout trial; most author-declared leaves, earliest source line tie break",
        "clinical_adjudication": False, "full_protocol_confirmation_allowed": False, "world_cap": 129, "cases": cases}, indent=2), encoding="utf-8")
    print(len(cases), "public clauses;", sum(len(c["world_values"]) for c in cases), "synthetic observations")


if __name__ == "__main__":
    main()
