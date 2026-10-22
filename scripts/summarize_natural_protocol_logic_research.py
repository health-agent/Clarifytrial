"""Preserve synthetic component results and aggregate paired replay diagnostics."""
from __future__ import annotations

import argparse
import csv
from copy import deepcopy
import json
from pathlib import Path

from run_natural_protocol_logic_research import summarize


VARIANTS = ("scripted-old", "scripted-new", "model-old", "model-new",
            "old-proposals-new-code", "new-proposals-old-code")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--suite", choices=("synthetic", "public"), default="synthetic")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    summaries, cases = {}, []
    variants = VARIANTS if args.suite == "synthetic" else ("model-old", "model-v7", "old-proposals-v7",
        "old-proposals-v8-continuation", "v7-proposals-v8", "v7-proposals-flat")
    expected_count = 15 if args.suite == "synthetic" else 24
    for variant in variants:
        data = json.loads((args.run_root / variant / "results.json").read_text(encoding="utf-8"))
        if len(data["cases"]) != expected_count:
            raise ValueError("component run is incomplete: " + variant)
        summaries[variant] = summarize(data["cases"])
        for row in data["cases"]:
            summary = summarize([row])
            cases.append({"variant": variant, "case_id": row["case_id"],
                "error": row["error"], "criterion_count": row.get("criterion_count"),
                "numeric_count": row.get("numeric_count"), "logic_supported": row.get("supported"),
                **{k: v for k, v in summary.items() if k not in {"effective_models"}}})
        if variant in {"model-old", "model-new", "model-v7"}:
            data["source_root"] = {"model-old": "archived-c25e002", "model-new": "source-logic-v7", "model-v7": "archived-24ee3b3"}[variant]
            data["summary"] = summaries[variant]
            if args.suite == "public":
                data["assessment_scope"] = "source_clause_numeric_component"
                data["full_protocol_confirmation_allowed"] = False
            for row in data["cases"]:
                if row["usage"]:
                    for key in ("request_id", "thread_id", "turn_id"):
                        row["usage"].pop(key, None)
            (args.output / (variant + ".json")).write_text(json.dumps(data, indent=2), encoding="utf-8")
        if args.suite == "public" and variant == "model-old":
            abstained = deepcopy(data["cases"])
            for row in abstained:
                row["usage"] = None
                for world in row["worlds"]:
                    world["observed"] = "not_confirmed"
            summaries["always-abstain"] = summarize(abstained)
    (args.output / "summary.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    with (args.output / "case_results.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(cases[0]))
        writer.writeheader()
        writer.writerows(cases)
    for variant, summary in summaries.items():
        print(variant, json.dumps(summary))


if __name__ == "__main__":
    main()
