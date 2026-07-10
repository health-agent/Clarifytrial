"""Demo: deterministic criteria parsing on mock trial protocols.

Renders each mock protocol into a raw trial text string (description +
criteria sections), runs the offline Criteria Parser Agent fallback,
validates the results, and writes outputs/criteria_parser_demo.md.

Usage (from any cwd):

    python scripts/run_criteria_parser_demo.py

No LLM calls, no network, no API keys. Synthetic data only; not medical
advice. Console output is ASCII-only (Windows cp949 console).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.criteria_parser import parse_trial_criteria_from_text  # noqa: E402
from models import (  # noqa: E402
    Criterion,
    CriterionType,
    SyntheticTrialProtocolDataset,
    TrialProtocol,
)

EXAMPLES_DIR = PROJECT_ROOT / "examples"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
REPORT_PATH = OUTPUTS_DIR / "criteria_parser_demo.md"

BANNER = "[SYNTHETIC CRITERIA PARSER DEMO]"


def render_trial_text(protocol: TrialProtocol) -> str:
    """Render a structured mock protocol as a raw trial text string.

    The mock dataset stores protocols in structured form; a real source
    would provide raw text. eligibility_criteria_raw already contains the
    Inclusion/Exclusion sections.
    """
    parts = []
    if protocol.title:
        parts.append(protocol.title)
    if protocol.trial_description:
        parts.append(protocol.trial_description)
    parts.append(protocol.eligibility_criteria_raw)
    return "\n\n".join(parts)


def main() -> dict[str, list[Criterion]]:
    dataset = SyntheticTrialProtocolDataset.model_validate(
        json.loads(
            (EXAMPLES_DIR / "synthetic_trial_protocols.json").read_text(
                encoding="utf-8"
            )
        )
    )
    protocols = dataset.trials[:3]
    print(f"{BANNER} loaded {len(dataset.trials)} protocols, parsing {len(protocols)}.")

    parsed: dict[str, list[Criterion]] = {}
    report = [
        "# Criteria Parser Demo (SYNTHETIC)",
        "",
        "Deterministic offline parsing (no LLM, no API keys). Trial",
        "description is context only and never becomes a criterion.",
        "Not medical advice.",
        "",
    ]
    for protocol in protocols:
        trial_text = render_trial_text(protocol)
        context, criteria = parse_trial_criteria_from_text(
            protocol.trial_id, trial_text
        )
        parsed[protocol.trial_id] = criteria

        inclusion = [c for c in criteria if c.criterion_type == CriterionType.inclusion]
        exclusion = [c for c in criteria if c.criterion_type == CriterionType.exclusion]
        sample_vars = sorted({v for c in criteria for v in c.required_variables})
        print(
            f"{BANNER} {protocol.trial_id}: {len(inclusion)} inclusion, "
            f"{len(exclusion)} exclusion, variables: "
            f"{', '.join(sample_vars[:6]) or 'none'}."
        )

        report += [
            f"## {protocol.trial_id}",
            "",
            f"- Description (context only): {context.description[:120]}...",
            f"- Inclusion criteria parsed: {len(inclusion)}",
            f"- Exclusion criteria parsed: {len(exclusion)}",
            "",
            "| criterion_id | type | required_variables | text |",
            "|---|---|---|---|",
        ]
        report += [
            f"| {c.criterion_id} | {c.criterion_type.value} "
            f"| {', '.join(c.required_variables) or '-'} | {c.text} |"
            for c in criteria
        ]
        report.append("")

    OUTPUTS_DIR.mkdir(exist_ok=True)
    REPORT_PATH.write_text("\n".join(report), encoding="utf-8")
    print(f"{BANNER} wrote {REPORT_PATH.relative_to(PROJECT_ROOT)}.")
    return parsed


if __name__ == "__main__":
    main()
