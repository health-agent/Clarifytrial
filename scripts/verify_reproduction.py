from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

from build_policy_scale_tables import (
    _burden_paired_rows,
    _burden_rows,
    _route_choice_rows,
    _write_csv,
)


COMMON_FACT_FILES = {
    "budget-1-paired-comparisons.csv": "public_protocol_common_facts_known_budget1.csv",
    "direct-transition-patient-differences.csv": "public_protocol_common_facts_known_patient_differences.csv",
    "direct-transition-summary.csv": "public_protocol_common_facts_known_direct_transition.csv",
    "paired-auc-comparisons.csv": "public_protocol_common_facts_known_auc.csv",
    "policy-metrics.csv": "public_protocol_common_facts_known_policy_metrics.csv",
    "question-category-counts.csv": "public_protocol_common_facts_known_question_categories.csv",
}

POLICY_SCALE_FILES = {
    "disease-level-sensitivity-summary.csv": "public_protocol_disease_level_sensitivity_summary.csv",
    "disease-level-sensitivity.csv": "public_protocol_disease_level_sensitivity.csv",
    "heldout-missing-fact-effects.csv": "public_protocol_missing_fact_effects.csv",
    "known-age-paired-comparisons.csv": "public_protocol_known_age_paired_comparisons.csv",
    "known-age-policy-metrics.csv": "public_protocol_known_age_policy_metrics.csv",
    "paired-budget-auc-comparisons.csv": "public_protocol_paired_budget_auc.csv",
    "paired-comparisons.csv": "public_protocol_paired_budget_comparisons.csv",
    "policy-metrics.csv": "public_protocol_policy_metrics.csv",
    "shared-degree-effect-contrasts.csv": "public_protocol_shared_degree_effect_contrasts.csv",
    "shared-degree-effects.csv": "public_protocol_shared_degree_effects.csv",
}


class ReproductionMismatch(RuntimeError):
    pass


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _compare_file_sets(
    generated_root: Path,
    evidence_root: Path,
    mapping: dict[str, str],
) -> list[dict[str, Any]]:
    rows = []
    for generated_name, evidence_name in mapping.items():
        generated = generated_root / generated_name
        evidence = evidence_root / evidence_name
        if not generated.is_file():
            raise ReproductionMismatch(f"generated file is missing: {generated}")
        if not evidence.is_file():
            raise ReproductionMismatch(f"evidence file is missing: {evidence}")
        generated_digest = _sha256(generated)
        evidence_digest = _sha256(evidence)
        if generated_digest != evidence_digest:
            raise ReproductionMismatch(
                f"file differs from committed evidence: {generated_name}"
            )
        rows.append(
            {
                "generated": str(generated),
                "evidence": str(evidence),
                "sha256": generated_digest,
                "exact_match": True,
            }
        )
    return rows


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _verify_route_and_burden(
    *,
    route_summary_path: Path,
    burden_summary_path: Path,
    evidence_root: Path,
    derived_root: Path,
) -> list[dict[str, Any]]:
    route_profiles, route_comparisons = _route_choice_rows(
        _read_json(route_summary_path)
    )
    burden_summary = _read_json(burden_summary_path)
    derived_root.mkdir(parents=True, exist_ok=True)
    _write_csv(derived_root / "route_choice_profile_results.csv", route_profiles)
    _write_csv(
        derived_root / "route_choice_paired_differences.csv", route_comparisons
    )
    _write_csv(
        derived_root / "burden_ablation_three_steps.csv",
        _burden_rows(burden_summary),
    )
    _write_csv(
        derived_root / "burden_ablation_paired_inference.csv",
        _burden_paired_rows(burden_summary),
    )
    mapping = {
        name: name
        for name in (
            "route_choice_profile_results.csv",
            "route_choice_paired_differences.csv",
            "burden_ablation_three_steps.csv",
            "burden_ablation_paired_inference.csv",
        )
    }
    return _compare_file_sets(derived_root, evidence_root, mapping)


def _verify_structural(
    run_root: Path,
    evidence_root: Path,
    budgets: list[int],
) -> list[dict[str, Any]]:
    committed = {
        (
            int(row["budget"]),
            row["subgroup"],
            row["evaluation_distribution"],
        ): row
        for row in _read_csv(evidence_root / "simple_vs_random_subgroups.csv")
        if row["suite"] == "synthetic_graph_stress"
        and row["candidate_policy_id"] == "clarifytrial_rule_v1"
        and row["baseline_policy_id"] == "random"
    }
    verified = []
    for budget in budgets:
        summary_path = run_root / f"budget-{budget}" / "structural-1800" / "summary.json"
        summary = _read_json(summary_path)
        expected_metadata = {
            "seed": 20260830,
            "random_policy_seed": 20260830,
            "structures_per_topology": 200,
            "structure_count": 1800,
            "structure_state_count": 57600,
            "policy_count": 12,
            "policy_state_evaluation_count": 691200,
            "action_budget": budget,
        }
        for key, expected in expected_metadata.items():
            if summary.get(key) != expected:
                raise ReproductionMismatch(
                    f"unexpected structural metadata {key}: "
                    f"{summary.get(key)!r} != {expected!r}"
                )
        metrics = {
            (
                row["topology"],
                row["evaluation_distribution"],
                row["policy_id"],
            ): row["expected_trial_recovery"]
            for row in summary["topology_metrics"]
        }
        for topology in summary["topologies"]:
            for distribution in ("similar_heldout", "shifted_heldout"):
                evidence = committed[(budget, topology, distribution)]
                candidate = metrics[
                    (topology, distribution, "clarifytrial_rule_v1")
                ]
                baseline = metrics[(topology, distribution, "random")]
                expected_candidate = float(evidence["candidate_score"])
                expected_baseline = float(evidence["baseline_score"])
                if candidate != expected_candidate or baseline != expected_baseline:
                    raise ReproductionMismatch(
                        "structural result differs from committed evidence: "
                        f"budget={budget} topology={topology} "
                        f"distribution={distribution}"
                    )
                verified.append(
                    {
                        "budget": budget,
                        "topology": topology,
                        "evaluation_distribution": distribution,
                        "candidate_score": candidate,
                        "baseline_score": baseline,
                        "exact_match": True,
                    }
                )
    return verified


def _git_commit() -> str | None:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=False,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare a deterministic ClarifyTrial rerun with committed evidence."
    )
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--common-facts", required=True, type=Path)
    parser.add_argument("--policy-scale", required=True, type=Path)
    parser.add_argument("--route-summary", required=True, type=Path)
    parser.add_argument("--burden-summary", required=True, type=Path)
    parser.add_argument("--structural-run-root", type=Path)
    parser.add_argument("--structural-budgets", nargs="*", type=int, default=[])
    parser.add_argument(
        "--evidence-root",
        type=Path,
        default=Path("docs/internal/results/presentation-evidence-v2"),
    )
    args = parser.parse_args()

    report = {
        "result": "verified",
        "git_commit": _git_commit(),
        "python": sys.version,
        "platform": platform.platform(),
        "exact_file_matches": [
            *_compare_file_sets(
                args.common_facts, args.evidence_root, COMMON_FACT_FILES
            ),
            *_compare_file_sets(
                args.policy_scale, args.evidence_root, POLICY_SCALE_FILES
            ),
            *_verify_route_and_burden(
                route_summary_path=args.route_summary,
                burden_summary_path=args.burden_summary,
                evidence_root=args.evidence_root,
                derived_root=args.output_root / "verification-derived",
            ),
        ],
        "structural_matches": (
            _verify_structural(
                args.structural_run_root,
                args.evidence_root,
                args.structural_budgets,
            )
            if args.structural_budgets and args.structural_run_root
            else []
        ),
        "scope": (
            "Deterministic public-protocol, patient-route, burden, and synthetic "
            "structure evidence. External-model observations are excluded."
        ),
    }
    report_path = args.output_root / "reproduction-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"verified files: {len(report['exact_file_matches'])}")
    print(f"verified structural rows: {len(report['structural_matches'])}")
    print(f"report: {report_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (FileNotFoundError, KeyError, ReproductionMismatch) as exc:
        print(f"reproduction verification failed: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
