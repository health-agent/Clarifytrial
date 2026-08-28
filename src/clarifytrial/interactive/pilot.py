"""Reproducible execution entry point for the 12-patient interactive pilot."""

from __future__ import annotations

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from ..disclaimer import read_medical_disclaimer
from ..llm import CodexSubscriptionModelPool, CodexSubscriptionStructuredModel
from .contracts import ExactPolicyObjective, InteractivePolicyRun
from .exact_policy import ExactDecisionTreePolicy, build_uniform_binary_scenarios
from .pilot_cases import build_interactive_pilot_cases
from .policies import (
    AuthoredOrderPolicy,
    ClarifyTrialRulePolicy,
    ImpactCostPolicy,
    ModelQuestionPolicy,
    NoQuestionPolicy,
    RandomQuestionPolicy,
    WidestImpactPolicy,
)
from .runner import run_interactive_policy, summarize_interactive_runs


def _medical_disclaimer() -> str:
    return read_medical_disclaimer()


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    temporary.replace(path)


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
    temporary.replace(path)


def run_interactive_pilot(
    output_dir: str | Path,
    *,
    include_subscription_model: bool = False,
    case_concurrency: int = 3,
    timeout_seconds: float = 180,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Run fixed baselines and optionally the isolated Sol medium selector."""

    if case_concurrency not in {1, 2, 3}:
        raise ValueError("case_concurrency must be 1, 2, or 3")
    destination = Path(output_dir)
    cases = build_interactive_pilot_cases()
    deterministic_factories = (
        NoQuestionPolicy,
        AuthoredOrderPolicy,
        lambda: RandomQuestionPolicy(seed=20_260_821),
        WidestImpactPolicy,
        ImpactCostPolicy,
        ClarifyTrialRulePolicy,
    )
    runs: list[InteractivePolicyRun] = []
    for factory in deterministic_factories:
        for case in cases:
            policy = factory()
            runs.append(run_interactive_policy(case, policy))
        if progress is not None:
            progress(f"completed {runs[-1].policy_id}: {len(cases)} cases")

    for objective in ExactPolicyObjective:
        for case in cases:
            view = case.public_policy_view()
            policy = ExactDecisionTreePolicy(
                view,
                case.initial_patient_state(),
                build_uniform_binary_scenarios(case),
                objective,
            )
            runs.append(run_interactive_policy(case, policy))
        if progress is not None:
            progress(f"completed {runs[-1].policy_id}: {len(cases)} cases")

    if include_subscription_model:
        with CodexSubscriptionModelPool(
            size=case_concurrency,
            worker_factory=lambda: CodexSubscriptionStructuredModel(
                timeout_seconds=timeout_seconds
            ),
        ) as model:
            with ThreadPoolExecutor(
                max_workers=case_concurrency,
                thread_name_prefix="interactive-pilot",
            ) as executor:
                futures = {
                    executor.submit(
                        run_interactive_policy,
                        case,
                        ModelQuestionPolicy(model),
                    ): case.case_id
                    for case in cases
                }
                model_runs: list[InteractivePolicyRun] = []
                for future in as_completed(futures):
                    result = future.result()
                    model_runs.append(result)
                    if progress is not None:
                        progress(f"completed model case: {result.case_id}")
                runs.extend(sorted(model_runs, key=lambda item: item.case_id))

    policy_ids = sorted({item.policy_id for item in runs})
    summaries = [
        summarize_interactive_runs(
            item for item in runs if item.policy_id == policy_id
        )
        for policy_id in policy_ids
    ]
    _write_json(
        destination / "plan.json",
        {
            "protocol_id": "interactive-pilot-v3-exact-tree",
            "case_count": len(cases),
            "disease_groups": sorted({item.disease_group for item in cases}),
            "candidate_trials_per_case": 5,
            "hidden_facts_per_case": 5,
            "action_budget": 3,
            "case_ids": [item.case_id for item in cases],
            "policies": policy_ids,
            "subscription_model_included": include_subscription_model,
            "medical_disclaimer": _medical_disclaimer(),
        },
    )
    _write_jsonl(
        destination / "runs.jsonl",
        [item.model_dump(mode="json") for item in runs],
    )
    summary_path = destination / "summary.json"
    _write_json(
        summary_path,
        {
            "protocol_id": "interactive-pilot-v3-exact-tree",
            "case_count": len(cases),
            "run_count": len(runs),
            "summaries": [item.model_dump(mode="json") for item in summaries],
            "medical_disclaimer": _medical_disclaimer(),
        },
    )
    return summary_path
