"""Demo: deterministic criterion matching on synthetic data.

Chains the three implemented agent fallbacks end to end:
profile extraction -> criteria parsing -> criterion matching ->
effect derivation (locked rules) -> global missing-variable dedup.

Usage (from any cwd):

    python scripts/run_criterion_matching_demo.py

No LLM calls, no network, no API keys. Synthetic data only; not medical
advice. Console output is ASCII-only (Windows cp949 console).
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.criteria_parser import parse_trial_criteria_from_text  # noqa: E402
from agents.criterion_matching import match_criterion_against_patient  # noqa: E402
from agents.patient_profile_understanding import (  # noqa: E402
    extract_patient_profile_from_summary,
)
from models import (  # noqa: E402
    CriterionState,
    SyntheticPatientDataset,
    SyntheticTrialProtocolDataset,
)
from rules import deduplicate_missing_variables  # noqa: E402
from scripts.run_criteria_parser_demo import render_trial_text  # noqa: E402

EXAMPLES_DIR = PROJECT_ROOT / "examples"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
REPORT_PATH = OUTPUTS_DIR / "criterion_matching_demo.md"

BANNER = "[SYNTHETIC CRITERION MATCHING DEMO]"


def main() -> list[CriterionState]:
    patients = SyntheticPatientDataset.model_validate(
        json.loads((EXAMPLES_DIR / "synthetic_patients.json").read_text(encoding="utf-8"))
    )
    protocols = SyntheticTrialProtocolDataset.model_validate(
        json.loads(
            (EXAMPLES_DIR / "synthetic_trial_protocols.json").read_text(encoding="utf-8")
        )
    )

    case = patients.topics[0]  # S001: synthetic NSCLC vignette
    profile = extract_patient_profile_from_summary(case.num, case.title)
    trials = protocols.trials[:2]
    print(
        f"{BANNER} patient {profile.patient_id} vs trials "
        f"{', '.join(t.trial_id for t in trials)}."
    )

    states: list[CriterionState] = []
    for protocol in trials:
        _, criteria = parse_trial_criteria_from_text(
            protocol.trial_id, render_trial_text(protocol)
        )
        states.extend(match_criterion_against_patient(profile, c) for c in criteria)

    status_counts = Counter(s.criterion_match_status.value for s in states)
    effect_counts = Counter(s.eligibility_effect.value for s in states)
    pool = deduplicate_missing_variables(states)

    print(f"{BANNER} matched {len(states)} criteria.")
    print(f"{BANNER} statuses: {dict(status_counts)}")
    print(f"{BANNER} effects: {dict(effect_counts)}")
    print(f"{BANNER} missing variables (deduplicated): {', '.join(pool) or 'none'}")

    report = [
        "# Criterion Matching Demo (SYNTHETIC)",
        "",
        "Deterministic offline matching (no LLM, no API keys). Missing",
        "information becomes 'unknown' with a missing_variable_key -- never",
        "negative evidence. Effects derived only by rules.derive_eligibility_effect.",
        "Not medical advice.",
        "",
        f"- Patient: `{profile.patient_id}` -- {case.title}",
        f"- Trials: {', '.join(t.trial_id for t in trials)}",
        f"- Criteria matched: {len(states)}",
        f"- Status counts: {dict(status_counts)}",
        f"- Effect counts: {dict(effect_counts)}",
        f"- Missing variables (deduplicated globally): "
        f"{', '.join(pool) or 'none'}",
        "",
        "| criterion_id | type | status | effect | missing keys | evidence |",
        "|---|---|---|---|---|---|",
    ]
    for s in states:
        ev = "; ".join(x.text for x in s.evidence.sentences) if s.evidence else ""
        report.append(
            f"| {s.criterion_id} | {s.criterion_type.value} "
            f"| {s.criterion_match_status.value} | {s.eligibility_effect.value} "
            f"| {', '.join(s.missing_variable_keys) or '-'} | {ev or '-'} |"
        )
    report += [
        "",
        "## Deduplicated missing-variable pool",
        "",
    ]
    for key, item in pool.items():
        report.append(
            f"- `{key}`: criteria {item.affected_criterion_ids} "
            f"across trials {item.affected_trial_ids}"
        )
    report.append("")

    OUTPUTS_DIR.mkdir(exist_ok=True)
    REPORT_PATH.write_text("\n".join(report), encoding="utf-8")
    print(f"{BANNER} wrote {REPORT_PATH.relative_to(PROJECT_ROOT)}.")
    return states


if __name__ == "__main__":
    main()
