"""Build a descriptive report of facts shared by trials in each disease group."""

from __future__ import annotations

import argparse
from pathlib import Path

from clarifytrial.interactive.shared_fact_report import write_shared_fact_report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trial-set",
        type=Path,
        default=Path("data/public_protocol_benchmark_v1/trial_set.json"),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("runs/public-protocol-shared-facts-v1"),
    )
    args = parser.parse_args()

    report = write_shared_fact_report(
        trial_set_path=args.trial_set,
        output_dir=args.output_dir,
    )
    overall = report["overall"]
    print(args.output_dir / "shared-fact-report.json")
    print(args.output_dir / "shared-fact-report.md")
    print(
        "shared criteria: "
        f"{overall['criteria_whose_fact_is_used_by_at_least_2_trials']} "
        f"({overall['share_of_criteria_with_a_cross_trial_fact']:.1%})"
    )


if __name__ == "__main__":
    main()
