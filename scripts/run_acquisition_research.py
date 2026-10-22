"""Run the opt-in patient-feasible decision-planning research comparison."""

import argparse
from pathlib import Path

from clarifytrial.interactive.acquisition_evaluation import run_acquisition_research


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, default=Path("configs/interactive_public_benchmark_v1.json"))
    parser.add_argument("--output", type=Path, default=Path("runs/acquisition-research-20261022"))
    parser.add_argument("--max-cases", type=int)
    parser.add_argument("--budgets", nargs="+", type=int, default=[1, 2, 3])
    args = parser.parse_args()
    result = run_acquisition_research(
        config_path=args.config, output=args.output, max_cases=args.max_cases,
        budgets=args.budgets,
    )
    print(f"done: {result['patient_count']} patients, {result['raw_result_count']} runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
