"""Author-declared numeric/boolean source clauses preserving AND/OR and exceptions.

This expands the SAME 48 source protocols. It is exploratory reuse, not a new
untouched clinical benchmark or an automatic NLP accuracy evaluation. Declarations
below are inspectable source interpretations and have not been clinician-adjudicated.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from clarifytrial.datasets.protocol_scope import describe_protocol_scope
from clarifytrial.datasets.source_criteria import _normalize_comparators
from clarifytrial.datasets.source_expression import validate_declared_numeric_expression


def atom(code, op, threshold, unit):
    return {"fact_code":code,"operator":op,"threshold":float(threshold),"unit":unit}


def both(*children): return {"operator":"all","children":children}
def either(*children): return {"operator":"any","children":children}
def flag(code, value): return atom(code,"eq",value,"bool")
def interval(code, lower, upper, unit, upper_open=False):
    return both(atom(code,"gte",lower,unit),atom(code,"lt" if upper_open else "lte",upper,unit))


# Source line numbers refer to the preserved eligibility text, including blanks.
# The trees describe only the selected clinical/numeric clause, not all prose on
# each line. Phase, treatment history, measurement method, and adjudication
# qualifiers that are not executable remain explicitly outside this benchmark.
DECLARATIONS = [
    ("NCT07889648",[4],"inclusion",interval("hba1c",7.5,10.5,"%"),["HbA1c",">=7.5%","<=10.5%"]),
    ("NCT06072326",[10],"inclusion",interval("uacr_mg_g",50,3000,"mg/g",True),["creatinine ratio",">= 50","<3000"]),
    ("NCT06072326",[11],"inclusion",interval("egfr_ml_min_1_73m2",30,90,"mL/min/1.73m2",True),["eGFR",">=30","<90"]),
    ("NCT06072326",[13],"inclusion",interval("hba1c",6.5,10.5,"%",True),["HbA1c",">=6.5","<10.5"]),
    ("NCT07654374",[5],"inclusion",interval("hba1c",6.5,10,"%"),["HbA1c",">=6.5%","<=10%"]),
    ("NCT07884058",[3],"inclusion",interval("body_mass_index_kg_m2",25,34,"kg/m2"),["BMI between 25 and 34"]),
    ("NCT07058155",[5],"inclusion",interval("body_mass_index_kg_m2",35,85,"kg/m2"),["BMI >=35 and <=85"]),
    ("NCT07058155",[25],"exclusion",atom("lvef_percent","lt",25,"%"),["ejection fraction <25%"]),
    ("NCT06906640",[9,10,11],"inclusion",either(
        atom("uacr_mg_g","gt",300,"mg/g"), atom("egfr_ml_min_1_73m2","lt",45,"mL/min/1.73m2"),
        both(atom("uacr_mg_g","gte",30,"mg/g"),atom("egfr_ml_min_1_73m2","lt",60,"mL/min/1.73m2"))),
        ["UACR >300 mg/g or","eGFR <45","UACR >=30 mg/g with eGFR <60"]),
    ("NCT07115953",[3],"inclusion",atom("office_systolic_bp_mmhg","gte",140,"mmHg"),["office systolic blood pressure >=140"]),
    ("NCT07115953",[4],"inclusion",atom("office_diastolic_bp_mmhg","gte",90,"mmHg"),["office diastolic blood pressure >= 90"]),
    ("NCT07115953",[5],"inclusion",atom("home_systolic_bp_mmhg","gte",135,"mmHg"),["home blood pressure >=135"]),
    ("NCT07854561",[21],"exclusion",either(atom("systolic_bp_mmhg","gt",160,"mmHg"),
         atom("diastolic_bp_mmhg","gt",100,"mmHg")),["systolic blood pressure >160","or diastolic blood pressure >100"]),
    ("NCT07188805",[4],"inclusion",atom("lvef_percent","lte",50,"%"),["LVEF) <= 50%"]),
    ("NCT07214376",[12],"inclusion",either(
        both(atom("lvef_percent","lte",40,"%"),atom("ntprobnp_pg_ml","gte",600,"pg/mL")),
        both(atom("lvef_percent","gt",40,"%"),atom("ntprobnp_pg_ml","gte",200,"pg/mL"))),
        ["NT-proBNP >=600","LVEF <=40% or >=200","LVEF >40%"]),
    ("NCT07214376",[39],"exclusion",either(atom("egfr_ml_min_1_73m2","lt",30,"mL/min/1.73m2"),
         flag("dialysis",1)),["eGFR <30","or on dialysis"]),
    ("NCT07490067",[18],"exclusion",atom("systolic_bp_mmhg","lt",95,"mmHg"),["Systolic blood pressure < 95"]),
    ("NCT07828054",[11],"inclusion",either(atom("ecog_score","eq",0,"score"),atom("ecog_score","eq",1,"score")),["ECOG performance status of 0 or 1"]),
    ("NCT07828054",[17],"inclusion",both(
        atom("serum_albumin_g_dl","gte",2.5,"g/dL"),atom("alkaline_phosphatase_xuln","lte",2.5,"xULN"),atom("inr_ratio","lt",1.5,"ratio"),
        either(atom("bilirubin_xuln","lte",1.5,"xULN"),both(either(flag("liver_metastases",1),flag("gilbert_syndrome",1)),atom("bilirubin_xuln","lte",3,"xULN"))),
        either(both(atom("ast_xuln","lte",3,"xULN"),atom("alt_xuln","lte",3,"xULN")),
               both(flag("liver_metastases",1),atom("ast_xuln","lte",5,"xULN"),atom("alt_xuln","lte",5,"xULN")))),
        ["Serum albumin >= 2.5 g/dL","<= 3 in patients with liver metastases","Gilbert's disease","ALP) <= 2.5","ALT) <= 3","<= 5 in patients with liver metastases","INR) < 1.5"]),
    ("NCT07828054",[29],"exclusion",atom("creatinine_clearance_ml_min","lt",30,"mL/min"),["creatinine clearance <30"]),
    ("NCT04305834",[29,31],"inclusion",either(
        both(atom("alt_xuln","lte",3,"xULN"),atom("ast_xuln","lte",3,"xULN")),
        both(flag("liver_metastases",1),atom("alt_xuln","lt",5,"xULN"),atom("ast_xuln","lt",5,"xULN"))),
        ["absence of liver metastases","=< 3.0","If the patient has liver metastases","< 5 x ULN"]),
    ("NCT04305834",[33],"inclusion",atom("creatinine_clearance_ml_min","gte",30,"mL/min"),["clearance of >= 30"]),
    ("NCT04222413",[26],"inclusion",atom("absolute_neutrophil_count_10_9_l","gte",1,"10^9/L"),["ANC) >= 1.0 x 10(9)/L"]),
    ("NCT04222413",[27],"inclusion",atom("platelet_count_10_9_l","gte",100,"10^9/L"),["platelet count >= 100 x 10(9)/L"]),
    ("NCT05099172",[11],"inclusion",either(atom("ecog_score","eq",0,"score"),atom("ecog_score","eq",1,"score")),["ECOG) performance status 0 or 1"]),
    ("NCT05099172",[15],"inclusion",atom("hemoglobin_g_dl","gte",9,"g/dL"),["Hemoglobin >= 9.0 g/dL"]),
    ("NCT05099172",[17],"inclusion",atom("absolute_neutrophil_count_10_9_l","gte",1.5,"10^9/L"),["count >= 1.5","cells/L"]),
    ("NCT02133196",[6],"inclusion",either(atom("ecog_score","eq",0,"score"),atom("ecog_score","eq",1,"score")),["ECOG 0 or 1"]),
    ("NCT02133196",[14],"inclusion",dict(atom("absolute_neutrophil_count_10_9_l","gt",1,"10^9/L"),source_threshold=1000.,source_unit="/mm3",conversion="/mm3 divided by 1000 to 10^9/L"),["count > 1000/mm"]),
    ("NCT02133196",[17],"inclusion",dict(atom("platelet_count_10_9_l","gte",80,"10^9/L"),source_threshold=80000.,source_unit="/mm3",conversion="/mm3 divided by 1000 to 10^9/L"),["count >= 80,000/mm"]),
]

REJECTED_SOURCE_CLAUSES=[
    {"nct_id":"NCT07828054","source_line_numbers":[23],"reason":"Source describes uncontrolled diabetes but literally prints HbA1c <8.5%; do not silently reverse it to >8.5%. Clinical/source clarification needed."},
    {"nct_id":"NCT02133196","source_line_numbers":[24],"reason":"ALT/AST shorthand could be misread as a ratio; do not infer two bounds without adjudication."},
    {"nct_id":"NCT04305834","source_line_numbers":[32],"reason":"Gilbert exception includes a direct-bilirubin-within-normal-limits alternative; leaving that alternative out would change meaning."},
]


def build(source: Path, output: Path):
    data=json.loads(source.read_text(encoding="utf-8"))
    data["version"]="paper-source-subsets-v2-logic"
    data["scope"]="Selected numeric and explicitly declared boolean logic clauses, including ranges, alternatives and exceptions. Temporal, phase, method and other unstructured qualifiers remain outside scope; not complete clinical eligibility."
    data["relation_to_v1"]="Author-declared expansion of the same protocols and split; exploratory reuse, not new independent clinical validation. Patient vectors are regenerated for the expanded feature set."
    data["review"]="Inspectable author source declarations; not automatic NLP accuracy or independent clinician adjudication. Non-numeric phase/history/method qualifiers remain outside scope."
    data["rejected_source_clauses"]=REJECTED_SOURCE_CLAUSES
    by_id={t["nct_id"]:t for g in data["groups"] for t in g["trials"]}
    for trial in by_id.values():
        trial["eligibility_logic"]={"operator":"all","children":[{"operator":"criterion","criterion_id":r["criterion_id"]} for r in trial["criteria"]]}
        trial["source_declarations"]=[]
    for sequence,(tid,numbers,kind,expression,checks) in enumerate(DECLARATIONS,1):
        trial=by_id[tid]
        lines=trial["eligibility_text"].splitlines()
        quote="\n".join(lines[n-1].strip() for n in numbers)
        normalized=_normalize_comparators(quote).casefold()
        for check in checks:
            if _normalize_comparators(check).casefold() not in normalized:
                raise ValueError(f"source declaration {tid} lines {numbers} missing: {check}")
        validate_declared_numeric_expression(expression,quote)
        def compile_node(node):
            if "fact_code" in node:
                row={"criterion_id":f"{tid}:declared:{sequence}:{len(trial['criteria'])}","nct_id":tid,"group_id":next(g["group_id"] for g in data["groups"] if trial in g["trials"]),
                     "kind":kind,"line_number":numbers[0],"source_line_numbers":numbers,"source_text":quote,
                     "source_field":"eligibility_text","source_location":trial["source_url"]+"#participation-criteria",
                     "confidence":"author_declared_logic","fact_description":node["fact_code"],"acquisition_mode":"existing_official_result",**node}
                trial["criteria"].append(row)
                return {"operator":"criterion","criterion_id":row["criterion_id"]}
            operator=node["operator"]
            # Eligibility-facing leaves already invert exclusions; De Morgan
            # swaps their group connective when excluding a composite predicate.
            if kind=="exclusion":operator={"all":"any","any":"all"}[operator]
            return {"operator":operator,"children":[compile_node(c) for c in node["children"]]}
        tree=compile_node(expression)
        trial["eligibility_logic"]["children"].append(tree)
        trial["source_declarations"].append({"source_line_numbers":numbers,"source_text":quote,
            "kind":kind,"source_membership_expression":expression,"eligibility_expression":tree,
            "scope":"Selected numeric/boolean clause only; other semantics and measurement/phase requirements on the quoted line can remain unmodeled."})
    for trial in by_id.values():
        trial["scope"]=describe_protocol_scope(trial)
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps({split:{"trials":sum(t["split"]==split for t in by_id.values()),
        "scalar_leaves":sum(len(t["criteria"]) for t in by_id.values() if t["split"]==split),
        "single_leaf_trials":sum(len(t["criteria"])==1 for t in by_id.values() if t["split"]==split)} for split in ("development","heldout")},indent=2))


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--source",type=Path,default=Path("data/paper_source_benchmark_v1/protocol_subsets.json"))
    parser.add_argument("--output",type=Path,default=Path("data/paper_source_benchmark_v2/protocol_subsets.json"))
    args=parser.parse_args()
    build(args.source,args.output)
