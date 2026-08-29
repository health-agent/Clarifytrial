"""Run the frozen 50-patient public-protocol question-order evaluation."""

from __future__ import annotations

import argparse
from pathlib import Path

from clarifytrial.datasets.public_protocol_policy_scale import (
    run_public_protocol_policy_scale,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--trial-set",
        type=Path,
        default=Path("data/public_protocol_benchmark_v1/trial_set.json"),
    )
    parser.add_argument(
        "--patient-pairs",
        type=Path,
        default=Path("data/public_protocol_benchmark_v1/patient_pairs.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("runs/public-protocol-policy-scale-20260830"),
    )
    args = parser.parse_args()
    summary = run_public_protocol_policy_scale(
        trial_set_path=args.trial_set,
        patient_pairs_path=args.patient_pairs,
        output_dir=args.output,
        progress=print,
    )
    print(f"summary: {summary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
