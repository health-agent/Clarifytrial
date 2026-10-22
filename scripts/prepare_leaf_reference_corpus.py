"""Inventory externally authored Leaf references before selecting an NLP probe.

Inputs are downloaded public protocol annotations. No JavaScript is executed, no
patient data are present, and annotations are not silently corrected to the text.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import tokenize
import urllib.request
import zipfile

from clarifytrial.preparation.leaf_reference import parse_reference, read_reference, structural_profile


REVISION="461288aeba8b37fabd43bd7c55f0e1cb1bb10b9e"


def download_source(root: Path):
    root=root.resolve()
    root.mkdir(parents=True,exist_ok=True)
    request=urllib.request.Request(f"https://codeload.github.com/uw-bionlp/leaf-corpora/zip/{REVISION}",
        headers={"User-Agent":"ClarifyTrial-research"})
    archive=root/"upstream-source.zip"
    with urllib.request.urlopen(request,timeout=60) as response:
        archive.write_bytes(response.read())
    with zipfile.ZipFile(archive) as zipped:
        for entry in zipped.infolist():
            relative="/".join(entry.filename.split("/")[1:])
            if entry.is_dir() or not (relative.startswith("leaf_logical_forms/") or relative in {"LICENSE","README.md"}):
                continue
            target=(root/relative).resolve()
            if not target.is_relative_to(root):
                raise ValueError("upstream archive escaped its destination")
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(zipped.read(entry))


def prepare(root: Path, output: Path):
    output.mkdir(parents=True, exist_ok=True)
    counts=Counter(); records=[]; by_clause=defaultdict(list)
    for path in sorted((root/"leaf_logical_forms").rglob("*.js")):
        relative=path.relative_to(root).as_posix()
        row={"source_path":relative, "nct_id":path.stem.split("_")[0],
             "source_url":f"https://github.com/uw-bionlp/leaf-corpora/blob/{REVISION}/{relative}"}
        counts["annotation_files"] += 1
        try:
            reference=read_reference(path.read_text(encoding="utf-8-sig"))
            row.update(asdict(reference))
        except ValueError as error:
            row.update(status="header_error", error=str(error));counts["header_error"] += 1
            records.append(row);continue
        by_clause[(row["nct_id"],reference.kind,reference.raw_text)].append(row)
        try:
            row.update(structural_profile(parse_reference(reference.logical_form)))
            row["status"]="parsed_syntax"
            counts["parsed_syntax"] += 1
            counts["numeric_annotations"] += bool(row["numeric_filters"])
            counts["restricted_numeric_candidates"] += row["restricted_numeric_candidate"]
        except (ValueError, tokenize.TokenError) as error:
            row.update(status="unparsed_syntax", error=str(error));counts["unparsed_syntax"] += 1
        records.append(row)
    repeated=[]
    for key, versions in by_clause.items():
        if len(versions) < 2: continue
        syntaxes={row.get("canonical_syntax") for row in versions}
        repeated.append({"nct_id":key[0], "kind":key[1], "raw_text":key[2],
            "records":[row["source_path"] for row in versions], "same_syntax":None not in syntaxes and len(syntaxes)==1})
    counts.update(unique_protocols=len({row["nct_id"] for row in records}),
        unique_raw_clauses=len(by_clause), repeated_raw_clauses=len(repeated),
        repeated_syntax_disagreements=sum(not row["same_syntax"] for row in repeated))
    summary={"status":"prepared; no model evaluation or clinical adjudication",
        "retrieved_on":"2026-10-23", "source_repository":"https://github.com/uw-bionlp/leaf-corpora",
        "source_revision":REVISION, "license":"upstream repository MIT; retained in LICENSE.upstream",
        "scope":"Human-authored protocol logical forms; no patient records; annotation syntax is not clinical truth",
        "counts":dict(counts), "repeated_annotations":repeated}
    (output/"inventory.json").write_text(json.dumps(summary,indent=2,ensure_ascii=False),encoding="utf-8")
    with (output/"annotations.jsonl").open("w",encoding="utf-8",newline="\n") as stream:
        for row in records: stream.write(json.dumps(row,ensure_ascii=False)+"\n")
    shutil.copy2(root/"LICENSE",output/"LICENSE.upstream")
    print(json.dumps(summary["counts"],indent=2))
    return summary


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--download",action="store_true",help="Fetch the fixed public source revision into --source before preparing")
    args=parser.parse_args()
    if args.download:
        download_source(args.source)
    prepare(args.source,args.output)
