"""Validate the synthetic datasets against the Pydantic models.

Run from anywhere:

    python scripts/validate_synthetic_data.py

Loads examples/synthetic_patients.json, synthetic_trial_protocols.json and
synthetic_matching_scenarios.json, validates them, prints concise counts,
and writes outputs/synthetic_data_validation_summary.md.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from models import (  # noqa: E402
    Recommendation,
    SyntheticMatchingScenarioDataset,
    SyntheticPatientDataset,
    SyntheticTrialProtocolDataset,
)

EXAMPLES_DIR = PROJECT_ROOT / "examples"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"


def load_json(name: str) -> dict:
    return json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))


def main() -> int:
    patients = SyntheticPatientDataset.model_validate(
        load_json("synthetic_patients.json")
    )
    protocols = SyntheticTrialProtocolDataset.model_validate(
        load_json("synthetic_trial_protocols.json")
    )
    scenarios = SyntheticMatchingScenarioDataset.model_validate(
        load_json("synthetic_matching_scenarios.json")
    )

    labels_covered = sorted(
        {s.expected_recommendation.value for s in scenarios.scenarios}
    )
    all_labels = sorted(r.value for r in Recommendation)

    lines = [
        f"synthetic_patients.json         : OK ({len(patients.topics)} patient case summaries)",
        f"synthetic_trial_protocols.json  : OK ({len(protocols.trials)} mock trial protocols)",
        f"synthetic_matching_scenarios.json: OK ({len(scenarios.scenarios)} labeled scenarios)",
        f"recommendation labels covered   : {', '.join(labels_covered)}"
        + (" (all)" if labels_covered == all_labels else " (INCOMPLETE)"),
    ]
    print("\n".join(lines))

    OUTPUTS_DIR.mkdir(exist_ok=True)
    summary_path = OUTPUTS_DIR / "synthetic_data_validation_summary.md"
    summary_path.write_text(
        "\n".join(
            [
                "# Synthetic Data Validation Summary",
                "",
                f"Generated: {datetime.now(timezone.utc).isoformat()}",
                "",
                "All datasets are synthetic/mock. Patient summaries are natural-language",
                "inputs only (input-contract validation), NOT eligibility ground truth.",
                "Matching scenarios are separate labeled rule-validation examples.",
                "",
                "| Dataset | Status | Count |",
                "|---|---|---|",
                f"| synthetic_patients.json | valid | {len(patients.topics)} case summaries |",
                f"| synthetic_trial_protocols.json | valid | {len(protocols.trials)} trial protocols |",
                f"| synthetic_matching_scenarios.json | valid | {len(scenarios.scenarios)} scenarios |",
                "",
                f"Recommendation labels covered: {', '.join(labels_covered)}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print(f"Summary written to {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
