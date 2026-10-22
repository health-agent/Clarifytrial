"""Exploratory source-to-decision probe, using synthetic English clauses only.

Run against an archived source checkout with --source-root for an actual old
pipeline comparison. Or replay saved model proposals against current code. The
reference evaluates complete possible worlds, independently of CriterionLogic.
This is a component probe, not clinical validation or an independent NLP set.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import date, datetime, timezone
from itertools import product
import json
from pathlib import Path
import sys


UNITS = {"egfr": "mL/min/1.73m2", "hba1c": "%", "age": "years", "ecog": "score"}
DOMAINS = {"egfr": [20, 50], "hba1c": [6, 8], "age": [17, 25], "ecog": [0, 1, 2]}
ATOMS = [("eGFR >=30 mL/min/1.73m2", "egfr", "gte", 30),
         ("HbA1c <7%", "hba1c", "lt", 7), ("Age >=18 years", "age", "gte", 18)]
A, B, C = [[op, concept, value] for _, concept, op, value in ATOMS]


def cohort():
    cases = []

    def add(cid, kind, quote, formula, atoms=ATOMS[:2], domains=None):
        concepts = list(dict.fromkeys(atom[1] for atom in atoms))
        cases.append({"case_id": cid, "kind": kind,
                      "text": kind.title() + " Criteria:\n" + quote,
                      "quote": quote, "formula": formula, "atoms": atoms,
                      "domains": domains or {c: DOMAINS[c] for c in concepts}})

    for kind in ("inclusion", "exclusion"):
        for op in ("and", "or"):
            add(f"{kind}_{op}", kind, f" {op} ".join(a[0] for a in ATOMS[:2]), [op, A, B])
    add("nested_inclusion", "inclusion", f"({ATOMS[0][0]} or {ATOMS[1][0]}) and {ATOMS[2][0]}",
        ["and", ["or", A, B], C], ATOMS)
    add("nested_exclusion", "exclusion", f"({ATOMS[0][0]} and {ATOMS[1][0]}) or {ATOMS[2][0]}",
        ["or", ["and", A, B], C], ATOMS)
    for kind in ("inclusion", "exclusion"):
        add(f"two_of_three_{kind}", kind,
            "At least two of the following must apply:\n" + "\n".join("* " + a[0] for a in ATOMS),
            ["at_least", 2, A, B, C], ATOMS)
    quote = "eGFR >=30 and <60 mL/min/1.73m2"
    add("shared_range", "inclusion", quote, ["and", A, ["lt", "egfr", 60]],
        [(quote, "egfr", "gte", 30), (quote, "egfr", "lt", 60)],
        {"egfr": [20, 30, 59, 60]})
    quote = "ECOG performance status of 0 or 1."
    add("ecog_enumeration", "inclusion", quote, ["or", ["eq", "ecog", 0], ["eq", "ecog", 1]],
        [(quote, "ecog", "eq", 0), (quote, "ecog", "eq", 1)])
    quote = "Age between 18 and 70 years"
    add("age_interval", "inclusion", quote, ["and", ["gte", "age", 18], ["lte", "age", 70]],
        [(quote, "age", "gte", 18), (quote, "age", "lte", 70)], {"age": [17, 18, 70, 71]})
    add("separate_requirements", "inclusion", "\n".join("* " + a[0] for a in ATOMS),
        ["and", A, B, C], ATOMS)
    # No unambiguous complete numeric reference for these sources. We measure
    # decisive outputs versus abstention, not clinical correctness on them.
    add("unrepresented_if", "inclusion", "If liver metastases are present, " + ATOMS[0][0], None, ATOMS[:1])
    add("ambiguous_scope", "inclusion", ATOMS[0][0] + " or " + ATOMS[1][0] + " and " + ATOMS[2][0], None, ATOMS)
    add("unrepresented_infection", "inclusion", ATOMS[0][0] + " and no active infection.", None,
        [(ATOMS[0][0] + " and no active infection.", "egfr", "gte", 30)])
    return cases


def truth(node, values):
    op = node[0]
    if op == "not":
        return not truth(node[1], values)
    if op == "and":
        return all(truth(child, values) for child in node[1:])
    if op == "or":
        return any(truth(child, values) for child in node[1:])
    if op == "at_least":
        return sum(truth(child, values) for child in node[2:]) >= node[1]
    value, threshold = values[node[1]], node[2]
    return {"gte": value >= threshold, "gt": value > threshold, "lte": value <= threshold,
            "lt": value < threshold, "eq": value == threshold}[op]


def expected(case, values):
    if case["formula"] is None:
        return None
    references=case.get("reference_alternatives",[case["formula"]])
    outcomes=set()
    for reference in references:
        missing = [c for c, value in values.items() if value is None]
        possible = set()
        for completion in product(*(case["domains"][c] for c in missing)):
            full = {**values, **dict(zip(missing, completion))}
            member = truth(reference, full)
            possible.add(member if case["kind"] == "inclusion" else not member)
            if len(possible) == 2:
                break
        outcomes.add("not_confirmed" if len(possible) == 2 else ("confirmed" if possible.pop() else "ineligible"))
    # A reference convention ambiguity is not a patient-information unknown.
    return outcomes.pop() if len(outcomes)==1 else None


def fixture(case):
    # Flat atom proposals deliberately contain no relation hints. Both code
    # versions receive the same proposals; multilines require explicit grouping
    # from a model and are therefore conservatively withheld by the new code.
    return {"criteria": [{"kind": case["kind"], "statement": quote, "source_quote": quote,
                           "numeric_constraint": {"concept": case.get("fact_labels", {}).get(concept, concept), "operator": op,
                                                  "threshold": value, "unit": case.get("units", UNITS)[concept]}}
                          for quote, concept, op, value in case["atoms"]]}


def world_values(case):
    if "world_values" in case:
        return case["world_values"]
    return [dict(zip(case["domains"], values))
            for values in product(*([None, *domain] for domain in case["domains"].values()))]


def summarize(rows):
    worlds = [w for row in rows for w in row["worlds"] if w["expected"] is not None]
    resolvable = [w for w in worlds if w["expected"] != "not_confirmed"]
    unsupported = [w for row in rows for w in row["worlds"] if w["expected"] is None]
    scored_cases = [row for row in rows if row["reference_formula"] is not None]
    return {
        "cases": len(rows), "failed_cases": sum(r["error"] is not None for r in rows),
        "scorable_cases": len(scored_cases), "scorable_worlds": len(worlds),
        "correct_worlds": sum(w["expected"] == w["observed"] for w in worlds),
        "all_worlds_correct_cases": sum(bool(r["worlds"]) and all(w["expected"] == w["observed"] for w in r["worlds"]) for r in scored_cases),
        "mean_case_agreement": (sum(sum(w["expected"] == w["observed"] for w in r["worlds"])/len(r["worlds"])
                                     for r in scored_cases if r["worlds"])/len(scored_cases)) if scored_cases else None,
        "resolvable_worlds": len(resolvable),
        "correct_resolved": sum(w["expected"] == w["observed"] for w in resolvable),
        "abstained_resolvable": sum(w["observed"] == "not_confirmed" for w in resolvable),
        "failed_resolvable": sum(w["observed"] == "preparation_failed" for w in resolvable),
        "false_confirmed": sum(w["expected"] == "ineligible" and w["observed"] == "confirmed" for w in worlds),
        "false_excluded": sum(w["expected"] == "confirmed" and w["observed"] == "ineligible" for w in worlds),
        "prematurely_decisive": sum(w["expected"] == "not_confirmed" and w["observed"] in {"confirmed", "ineligible"} for w in worlds),
        "unscored_scope_worlds": len(unsupported),
        "decisive_on_unscored_scope": sum(w["observed"] in {"confirmed", "ineligible"} for w in unsupported),
        "provider_total_tokens": sum((r["usage"] or {}).get("total_tokens") or 0 for r in rows),
        "effective_models": sorted({r["usage"]["model_id"] for r in rows if r["usage"]}),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("scripted", "model", "replay"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-root", type=Path)
    parser.add_argument("--cases", type=Path, help="Alternative fixed source-clause cohort; no labels enter model payloads.")
    parser.add_argument("--replay", type=Path)
    parser.add_argument("--strip-logic-draft", action="store_true",
                        help="Replay new atom proposals through the old list-only schema; remove only local keys/groups.")
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--effort", default="medium")
    parser.add_argument("--runtime-catalog", type=Path)
    args = parser.parse_args()
    cases = json.loads(args.cases.read_text(encoding="utf-8"))["cases"] if args.cases else cohort()
    source_root = (args.source_root or Path(__file__).resolve().parents[1]).resolve()
    sys.path.insert(0, str(source_root / "src"))
    from clarifytrial.contracts import CriterionAssessment, EvidenceFact, PatientState
    from clarifytrial.llm import ScriptedStructuredModel
    from clarifytrial.llm.prompts import FilePromptLoader
    from clarifytrial.mechanical_checks import evaluate_criterion
    from clarifytrial.preparation.contracts import TrialProtocolSource
    from clarifytrial.preparation.trial_protocol import TrialProtocolStructurerAgent, structure_trial_protocol
    from clarifytrial.trace import TraceRecorder
    from clarifytrial.workflow.patient_screening_rules import aggregate_screening_trial

    args.output.mkdir(parents=True, exist_ok=True)
    replay = {}
    if args.replay:
        replay = {r["case_id"]: r for r in json.loads(args.replay.read_text(encoding="utf-8"))["cases"]}
    model = None
    runtime = None
    if args.mode == "model":
        import clarifytrial.llm.codex_subscription as cs
        if args.runtime_catalog:
            # Only the process-local pinned SDK sees these compatibility options.
            # User configuration is never modified. Catalog contains no secrets.
            cs._HARDENED_CONFIG_OVERRIDES = (*cs._HARDENED_CONFIG_OVERRIDES,
                "features.context_management=false",
                "model_catalog_json=" + json.dumps(str(args.runtime_catalog.resolve())))
        model = cs.CodexSubscriptionStructuredModel(model_id=args.model, effort=args.effort,
            prompt_loader=FilePromptLoader(source_root), timeout_seconds=240, max_retries=0)
        model.available_models()
        runtime = asdict(model.runtime_metadata())
    rows = []
    output_file = args.output / "results.json"
    # Resume completed cases without repeating billable calls. Do not mix modes.
    if output_file.exists():
        previous = json.loads(output_file.read_text(encoding="utf-8"))
        if previous["mode"] != args.mode or previous["source_root"] != str(source_root):
            raise ValueError("output belongs to a different run")
        rows = previous["cases"]
        current = {case["case_id"]: case for case in cases}
        if any(row["case_id"] not in current or row["source_text"] != current[row["case_id"]]["text"]
               or row["reference_formula"] != current[row["case_id"]]["formula"]
               or row.get("reference_alternatives") != current[row["case_id"]].get("reference_alternatives") for row in rows):
            raise ValueError("output belongs to a different source cohort")
    done = {r["case_id"] for r in rows}
    try:
        for case in cases:
            if case["case_id"] in done:
                continue
            trace = TraceRecorder(case["case_id"])
            row = {"case_id": case["case_id"], "source_text": case["text"], "reference_formula": case["formula"],
                   "reference_alternatives":case.get("reference_alternatives"),
                   "domains": case["domains"], "worlds": [], "error": None}
            try:
                selected_model = model
                if args.mode != "model":
                    proposed = fixture(case) if args.mode == "scripted" else replay[case["case_id"]]["proposal"]
                    if proposed is None:
                        raise ValueError("replayed model did not return a valid proposal")
                    if args.strip_logic_draft:
                        proposed = {"criteria": [{k: v for k, v in item.items() if k != "criterion_key"}
                                                  for item in proposed["criteria"]]}
                    selected_model = ScriptedStructuredModel({"trial_protocol_structurer": lambda _, p=proposed: p})
                prepared = structure_trial_protocol(TrialProtocolSource(trial_id=case["case_id"],
                    title=case.get("title", "Synthetic clause probe"), eligibility_text=case["text"],
                    source_location=case.get("source_location", "synthetic://" + case["case_id"])),
                    TrialProtocolStructurerAgent(selected_model), trace=trace)
                row["supported"] = prepared.trial.protocol_logic_supported
                row["issues"] = prepared.trial.protocol_logic_issues
                row["numeric_count"] = sum(c.numeric_constraint is not None for c in prepared.trial.criteria)
                row["criterion_count"] = len(prepared.trial.criteria)
                row["compiled_logic"] = (prepared.trial.eligibility_logic.model_dump(mode="json")
                                         if prepared.trial.eligibility_logic is not None else None)
                for values in world_values(case):
                    state = PatientState(patient_id="synthetic-world", as_of=datetime(2026, 10, 22, 12, tzinfo=timezone.utc),
                        facts=[EvidenceFact(evidence_id=c, statement="Synthetic " + c, source_type="medical_record",
                            source_location="synthetic://patient", verification_status="verified", event_date=date(2026, 10, 22),
                            concept=case.get("fact_labels", {}).get(c, c), value=v,
                            unit=case.get("units", UNITS)[c]) for c, v in values.items() if v is not None])
                    assessments = {}
                    for criterion in prepared.trial.criteria:
                        checked = evaluate_criterion(criterion, state)
                        assessments[criterion.criterion_id] = CriterionAssessment(criterion_id=criterion.criterion_id,
                            criterion_source_location=criterion.source_location, clinical_status=checked.clinical_status,
                            evidence_sufficiency=checked.evidence_sufficiency, evidence_ids=checked.evidence_ids,
                            rationale="Deterministic synthetic probe; opaque prose remains unknown.")
                    observed = aggregate_screening_trial(trial=prepared.trial, assessments=assessments,
                        evidence_requests=[], patient_state=state).confirmation_status.value
                    row["worlds"].append({"values": values, "expected": expected(case, values), "observed": observed})
            except Exception as error:
                # Never serialize raw provider errors, which can contain payloads.
                row["error"] = type(error).__name__
                row["worlds"] = [{"values": values, "expected": expected(case, values), "observed": "preparation_failed"}
                                 for values in world_values(case)]
            calls = [e for e in trace.events if e.event == "structured_model_completed"]
            row["proposal"] = calls[0].output["response"] if calls else None
            row["usage"] = calls[0].usage if calls else None
            trace.write_jsonl(args.output / (case["case_id"] + ".jsonl"))
            rows.append(row)
            output_file.write_text(json.dumps({"mode": args.mode, "source_root": str(source_root), "runtime": runtime,
                "model": args.model if args.mode == "model" else None, "effort": args.effort if args.mode == "model" else None,
                "strip_logic_draft": args.strip_logic_draft,
                "cases_file": str(args.cases) if args.cases else None,
                "scope": "Exploratory source-clause component probe against supplied numeric reference; synthetic values, no clinical inference or prose judgment model.",
                "summary": summarize(rows), "cases": rows}, indent=2), encoding="utf-8")
            print(case["case_id"], row["error"] or (str(len(row["worlds"])) + " worlds"), flush=True)
    finally:
        if model is not None:
            model.close()


if __name__ == "__main__":
    main()
