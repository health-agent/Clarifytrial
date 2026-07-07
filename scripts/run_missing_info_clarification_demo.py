"""Demo: missing-information detection + global clarification queue.

Chains the implemented offline pipeline: profile extraction -> criteria
parsing -> criterion matching -> global missing-variable pool (session-
level dedup with traceability + priority) -> global clarification queue
(one question per variable).

Usage (from any cwd):

    python scripts/run_missing_info_clarification_demo.py

No LLM calls, no network, no API keys. Synthetic data only; no
eligibility is decided here; not medical advice. ASCII-only console
output (Windows cp949 console).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.clarification_question import build_global_clarification_queue  # noqa: E402
from agents.criteria_parser import parse_trial_criteria_from_text  # noqa: E402
from agents.criterion_matching import match_criterion_against_patient  # noqa: E402
from agents.missing_information_detection import (  # noqa: E402
    build_global_missing_variable_pool,
)
from agents.patient_profile_understanding import (  # noqa: E402
    extract_patient_profile_from_summary,
)
from models import (  # noqa: E402
    CriterionMatchStatus,
    FollowUpQuestion,
    SyntheticPatientDataset,
    SyntheticTrialProtocolDataset,
)
from scripts.run_criteria_parser_demo import render_trial_text  # noqa: E402

EXAMPLES_DIR = PROJECT_ROOT / "examples"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
REPORT_PATH = OUTPUTS_DIR / "missing_info_clarification_demo.md"

BANNER = "[SYNTHETIC MISSING-INFO DEMO]"


def main() -> list[FollowUpQuestion]:
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

    states = []
    for protocol in trials:
        _, criteria = parse_trial_criteria_from_text(
            protocol.trial_id, render_trial_text(protocol)
        )
        states.extend(match_criterion_against_patient(profile, c) for c in criteria)

    unknown_states = [
        s for s in states if s.criterion_match_status == CriterionMatchStatus.unknown
    ]
    keys_before_dedup = [k for s in unknown_states for k in s.missing_variable_keys]
    pool = build_global_missing_variable_pool(states)
    questions = build_global_clarification_queue(pool, round_number=1)

    print(f"{BANNER} criterion states inspected: {len(states)}")
    print(f"{BANNER} unknown criteria: {len(unknown_states)}")
    print(f"{BANNER} missing variables before dedup: {len(keys_before_dedup)} "
          f"({', '.join(keys_before_dedup)})")
    print(f"{BANNER} after session-level dedup: {len(pool)} ({', '.join(pool)})")
    print(f"{BANNER} questions generated (one per variable): {len(questions)}")
    for key, item in pool.items():
        if len(item.affected_trial_ids) > 1:
            print(
                f"{BANNER} traceability example: '{key}' <- criteria "
                f"{item.affected_criterion_ids} across trials "
                f"{item.affected_trial_ids} (asked ONCE, priority={item.priority})."
            )
            break

    report = [
        "# Missing Information + Clarification Queue Demo (SYNTHETIC)",
        "",
        "Deterministic offline run (no LLM, no API keys). Missing information",
        "is never negative evidence; no eligibility is decided here.",
        "Not medical advice.",
        "",
        f"- Patient: `{profile.patient_id}`",
        f"- Trials: {', '.join(t.trial_id for t in trials)}",
        f"- Criterion states inspected: {len(states)} "
        f"({len(unknown_states)} unknown)",
        f"- Missing variable keys before dedup: {len(keys_before_dedup)}",
        f"- Pool items after session-level dedup: {len(pool)}",
        f"- Clarification questions generated: {len(questions)} (one per variable)",
        "",
        "## Global missing-variable pool (traceable, prioritized)",
        "",
        "| missing_variable_key | priority | source criteria | source trials |",
        "|---|---|---|---|",
    ]
    report += [
        f"| {item.missing_variable_key} | {item.priority} "
        f"| {', '.join(item.affected_criterion_ids)} "
        f"| {', '.join(item.affected_trial_ids)} |"
        for item in pool.values()
    ]
    report += [
        "",
        "## Global clarification queue (round 1 of max 3)",
        "",
        "| id | priority_rank | variable | question |",
        "|---|---|---|---|",
    ]
    report += [
        f"| {q.question_id} | {q.priority_rank} | {q.missing_variable_key} "
        f"| {q.question_text} |"
        for q in questions
    ]
    report.append("")

    OUTPUTS_DIR.mkdir(exist_ok=True)
    REPORT_PATH.write_text("\n".join(report), encoding="utf-8")
    print(f"{BANNER} wrote {REPORT_PATH.relative_to(PROJECT_ROOT)}.")
    return questions


if __name__ == "__main__":
    main()
