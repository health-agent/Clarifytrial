"""Demo: deterministic patient profile extraction on synthetic summaries.

Runs the offline heuristic fallback of the Patient Profile Understanding
Agent on a few synthetic case summaries, validates the results, and
writes outputs/patient_profile_extraction_demo.md.

Usage (from any cwd):

    python scripts/run_patient_profile_extraction_demo.py

No LLM calls, no network, no API keys. Synthetic data only; not medical
advice. Console output is ASCII-only (Windows cp949 console).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.patient_profile_understanding import (  # noqa: E402
    extract_patient_profile_from_summary,
)
from models import PatientProfile, SyntheticPatientDataset  # noqa: E402

EXAMPLES_DIR = PROJECT_ROOT / "examples"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
REPORT_PATH = OUTPUTS_DIR / "patient_profile_extraction_demo.md"

BANNER = "[SYNTHETIC EXTRACTION DEMO]"


def main() -> list[PatientProfile]:
    dataset = SyntheticPatientDataset.model_validate(
        json.loads(
            (EXAMPLES_DIR / "synthetic_patients.json").read_text(encoding="utf-8")
        )
    )
    cases = dataset.topics[:3]
    print(f"{BANNER} loaded {len(dataset.topics)} summaries, extracting {len(cases)}.")

    profiles: list[PatientProfile] = []
    report = [
        "# Patient Profile Extraction Demo (SYNTHETIC)",
        "",
        "Deterministic offline heuristic extraction (no LLM, no API keys).",
        "Unknown fields are preserved as null/'unknown'. Not medical advice.",
        "",
    ]
    for case in cases:
        profile = extract_patient_profile_from_summary(case.num, case.title)
        # Round-trip validation against the Pydantic model.
        PatientProfile.model_validate(profile.model_dump())
        profiles.append(profile)

        print(
            f"{BANNER} {case.num}: age={profile.age}, sex={profile.sex}, "
            f"diagnosis={profile.variables['diagnosis']}, "
            f"stage={profile.variables['stage']} -> valid PatientProfile."
        )
        report += [
            f"## {case.num}",
            "",
            f"- Summary (input only): {case.title}",
            f"- Extracted age: {profile.age}",
            f"- Extracted sex: {profile.sex}",
            f"- Conditions: {', '.join(profile.conditions) or 'none detected'}",
            f"- Variables: `{json.dumps(profile.variables)}`",
            "- Validation: passed (PatientProfile)",
            "",
        ]

    OUTPUTS_DIR.mkdir(exist_ok=True)
    REPORT_PATH.write_text("\n".join(report), encoding="utf-8")
    print(f"{BANNER} wrote {REPORT_PATH.relative_to(PROJECT_ROOT)}.")
    return profiles


if __name__ == "__main__":
    main()
