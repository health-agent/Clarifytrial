"""Compare clarification policies on the frozen natural-record pairs.

All policies share the same record extraction.  The only experimental change
is whether questions are allowed and, if so, how the next fact is selected.
Verified answers come from the hidden sufficient member of the synthetic pair.
"""

from __future__ import annotations

import json
from math import comb
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from ..contracts import (
    ConfirmationStatus,
    EvidenceFact,
    EvidenceSufficiency,
    EvidenceSourceType,
    NextEvidenceRequest,
    PatientState,
    VerificationStatus,
)
from ..mechanical_checks import evaluate_criterion
from ..interactive.coverage_policy import choose_fact_from_unresolved_sets
from .integrity import portable_text_sha256
from .natural_patient_pairs import (
    _decisions,
    _fact_route,
    _normalized_criteria,
    _trial_criteria,
    load_natural_patient_generation_config,
)
from .natural_structure_evaluation import ExtractedNaturalRecord


PolicyName = Literal[
    "no_questions",
    "fixed_source_order",
    "clarifytrial_rule_v1",
    "clarifytrial_rule_v2_resolve_first",
    "clarifytrial_exact_coverage_v3",
]


def _decision_metrics(
    current: Sequence[Mapping[str, Any]], target: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    current_by_trial = {str(item["trial_id"]): item for item in current}
    target_by_trial = {str(item["trial_id"]): item for item in target}
    trial_ids = sorted(target_by_trial)
    exact = sum(
        (
            current_by_trial[item]["candidate_status"],
            current_by_trial[item]["confirmation_status"],
        )
        == (
            target_by_trial[item]["candidate_status"],
            target_by_trial[item]["confirmation_status"],
        )
        for item in trial_ids
    )
    candidate = sum(
        current_by_trial[item]["candidate_status"]
        == target_by_trial[item]["candidate_status"]
        for item in trial_ids
    )
    confirmation = sum(
        current_by_trial[item]["confirmation_status"]
        == target_by_trial[item]["confirmation_status"]
        for item in trial_ids
    )
    return {
        "trial_count": len(trial_ids),
        "exact_trial_status_count": exact,
        "candidate_status_match_count": candidate,
        "confirmation_status_match_count": confirmation,
        "trial_status_recovery": exact / len(trial_ids),
        "candidate_status_recovery": candidate / len(trial_ids),
        "confirmation_status_recovery": confirmation / len(trial_ids),
        "resolved_trial_count": sum(
            current_by_trial[item]["confirmation_status"]
            in {ConfirmationStatus.CONFIRMED.value, ConfirmationStatus.INELIGIBLE.value}
            for item in trial_ids
        ),
        "false_candidate_removals": sum(
            current_by_trial[item]["candidate_status"] == "remove"
            and target_by_trial[item]["candidate_status"] != "remove"
            for item in trial_ids
        ),
        "missed_candidate_removals": sum(
            current_by_trial[item]["candidate_status"] != "remove"
            and target_by_trial[item]["candidate_status"] == "remove"
            for item in trial_ids
        ),
        "premature_confirmations": sum(
            current_by_trial[item]["confirmation_status"] == "confirmed"
            and target_by_trial[item]["confirmation_status"] != "confirmed"
            for item in trial_ids
        ),
    }


def _requests(
    *,
    patient_id: str,
    pivotal_codes: Sequence[str],
    descriptions: Mapping[str, str],
    criteria_by_trial: Mapping[str, Sequence[Any]],
) -> list[NextEvidenceRequest]:
    criteria = [item for rows in criteria_by_trial.values() for item in rows]
    result = []
    for fact_code in pivotal_codes:
        _, actions = _fact_route(fact_code)
        related = [
            item.criterion_id
            for item in criteria
            if item.numeric_constraint is not None
            and item.numeric_constraint.concept.rsplit(":", 1)[-1] == fact_code
        ]
        result.append(
            NextEvidenceRequest(
                fact_id=f"{patient_id}:{fact_code}",
                description=f"{descriptions[fact_code]} 확인",
                related_criterion_ids=related,
                acceptable_actions=actions,
                reason="현재 값은 보이지만 참가 조건을 확정할 근거가 부족하다.",
            )
        )
    return result


def _gold_state(patient_id: str, episode: Mapping[str, Any], as_of: datetime) -> PatientState:
    return PatientState(
        patient_id=patient_id,
        as_of=as_of,
        facts=[EvidenceFact.model_validate(item) for item in episode["evidence"]],
    )


def _extracted_state(
    *,
    patient_id: str,
    group_id: str,
    record: Mapping[str, Any],
    result: Mapping[str, Any],
    as_of: datetime,
) -> PatientState:
    if result.get("status") != "completed":
        return PatientState(patient_id=patient_id, as_of=as_of, facts=[])
    output = ExtractedNaturalRecord.model_validate(result["output"])
    expected = {item["measurement_id"]: item for item in record["expected_facts"]}
    seen: set[str] = set()
    facts = []
    for item in output.facts:
        gold = expected.get(item.measurement_id)
        if gold is None or item.measurement_id in seen:
            continue
        seen.add(item.measurement_id)
        facts.append(
            EvidenceFact(
                evidence_id=f"{record['record_id']}:{item.measurement_id}:extracted",
                statement=(
                    f"합성 기록에서 읽은 {gold['fact_code']}: "
                    f"{item.value:g} {item.unit}"
                ),
                source_type=item.source_type,
                source_location=f"{record['record_id']}#{item.measurement_id}",
                event_date=(
                    date.fromisoformat(gold["event_date"])
                    if gold.get("event_date")
                    else None
                ),
                recorded_date=(
                    date.fromisoformat(gold["recorded_date"])
                    if gold.get("recorded_date")
                    else None
                ),
                verification_status=item.verification_status,
                concept=f"{group_id}:{gold['fact_code']}",
                value=item.value,
                unit=item.unit,
            )
        )
    return PatientState(patient_id=patient_id, as_of=as_of, facts=facts)


def _replace_fact_code(
    state: PatientState, fact_code: str, answers: Sequence[EvidenceFact]
) -> PatientState:
    kept = [
        item
        for item in state.facts
        if item.concept is None or item.concept.rsplit(":", 1)[-1] != fact_code
    ]
    return state.model_copy(update={"facts": [*kept, *answers]})


def _fixed_order(
    *,
    group_id: str,
    pivotal_codes: Sequence[str],
    normalized_rows: Sequence[Mapping[str, Any]],
) -> list[str]:
    pivotal = set(pivotal_codes)
    ordered = []
    for row in normalized_rows:
        fact_code = str(row["canonical_fact_code"])
        if (
            row["group_id"] == group_id
            and fact_code in pivotal
            and fact_code not in ordered
        ):
            ordered.append(fact_code)
    return [*ordered, *(item for item in pivotal_codes if item not in ordered)]


def _choose_clarifytrial_fact(
    *,
    remaining_codes: Sequence[str],
    decisions: Sequence[Mapping[str, Any]],
    criteria_by_trial: Mapping[str, Sequence[Any]],
) -> str | None:
    decision_by_trial = {str(item["trial_id"]): item for item in decisions}
    counts: dict[str, tuple[int, int, int]] = {}
    for fact_code in remaining_codes:
        related_trials = set()
        related_criteria = 0
        for trial_id, criteria in criteria_by_trial.items():
            if decision_by_trial[trial_id]["confirmation_status"] in {
                ConfirmationStatus.CONFIRMED.value,
                ConfirmationStatus.INELIGIBLE.value,
            }:
                continue
            matching = [
                item
                for item in criteria
                if item.numeric_constraint is not None
                and item.numeric_constraint.concept.rsplit(":", 1)[-1]
                == fact_code
            ]
            if matching:
                related_trials.add(trial_id)
                related_criteria += len(matching)
        route, _ = _fact_route(fact_code)
        route_cost = {"ASK_PATIENT": 1, "LOOKUP_RECORD": 2, "REQUEST_VERIFICATION": 3}[
            route.value
        ]
        counts[fact_code] = (len(related_trials), related_criteria, route_cost)
    useful = {key: value for key, value in counts.items() if value[0] > 0}
    if not useful:
        return None
    return min(
        useful,
        key=lambda key: (-useful[key][0], -useful[key][1], useful[key][2], key),
    )


def _choose_resolve_first_fact(
    *,
    remaining_codes: Sequence[str],
    decisions: Sequence[Mapping[str, Any]],
    criteria_by_trial: Mapping[str, Sequence[Any]],
    patient_state: PatientState,
) -> str | None:
    """Prefer a fact that can finish a trial immediately, then broad impact."""

    decision_by_trial = {str(item["trial_id"]): item for item in decisions}
    unresolved_by_trial: dict[str, set[str]] = {}
    for trial_id, criteria in criteria_by_trial.items():
        if decision_by_trial[trial_id]["confirmation_status"] in {
            ConfirmationStatus.CONFIRMED.value,
            ConfirmationStatus.INELIGIBLE.value,
        }:
            continue
        unresolved = set()
        for criterion in criteria:
            result = evaluate_criterion(criterion, patient_state)
            if (
                result.evidence_sufficiency is not EvidenceSufficiency.SUFFICIENT
                and criterion.numeric_constraint is not None
            ):
                unresolved.add(
                    criterion.numeric_constraint.concept.rsplit(":", 1)[-1]
                )
        unresolved_by_trial[trial_id] = unresolved

    scores: dict[str, tuple[int, int, int, int]] = {}
    for fact_code in remaining_codes:
        related_trials = {
            trial_id
            for trial_id, unresolved in unresolved_by_trial.items()
            if fact_code in unresolved
        }
        immediately_resolvable = sum(
            unresolved == {fact_code} for unresolved in unresolved_by_trial.values()
        )
        related_criteria = sum(
            item.numeric_constraint is not None
            and item.numeric_constraint.concept.rsplit(":", 1)[-1] == fact_code
            for trial_id in related_trials
            for item in criteria_by_trial[trial_id]
        )
        route, _ = _fact_route(fact_code)
        route_cost = {"ASK_PATIENT": 1, "LOOKUP_RECORD": 2, "REQUEST_VERIFICATION": 3}[
            route.value
        ]
        scores[fact_code] = (
            immediately_resolvable,
            len(related_trials),
            related_criteria,
            route_cost,
        )
    useful = {key: value for key, value in scores.items() if value[1] > 0}
    if not useful:
        return None
    return min(
        useful,
        key=lambda key: (
            -useful[key][0],
            -useful[key][1],
            -useful[key][2],
            useful[key][3],
            key,
        ),
    )


def _choose_exact_coverage_fact(
    *,
    remaining_codes: Sequence[str],
    remaining_budget: int,
    decisions: Sequence[Mapping[str, Any]],
    criteria_by_trial: Mapping[str, Sequence[Any]],
    patient_state: PatientState,
) -> str | None:
    """Search every small fact set and maximize trials closable in the budget."""

    if remaining_budget <= 0:
        return None
    decision_by_trial = {str(item["trial_id"]): item for item in decisions}
    unresolved_by_trial: dict[str, set[str]] = {}
    criterion_count_by_fact: dict[str, int] = defaultdict(int)
    for trial_id, criteria in criteria_by_trial.items():
        if decision_by_trial[trial_id]["confirmation_status"] in {
            ConfirmationStatus.CONFIRMED.value,
            ConfirmationStatus.INELIGIBLE.value,
        }:
            continue
        unresolved = set()
        for criterion in criteria:
            result = evaluate_criterion(criterion, patient_state)
            if (
                result.evidence_sufficiency is not EvidenceSufficiency.SUFFICIENT
                and criterion.numeric_constraint is not None
            ):
                fact_code = criterion.numeric_constraint.concept.rsplit(":", 1)[-1]
                unresolved.add(fact_code)
                criterion_count_by_fact[fact_code] += 1
        if unresolved:
            unresolved_by_trial[trial_id] = unresolved
    useful = [
        item
        for item in remaining_codes
        if any(item in facts for facts in unresolved_by_trial.values())
    ]
    if not useful:
        return None
    return choose_fact_from_unresolved_sets(
        unresolved_by_trial=unresolved_by_trial,
        related_criterion_count=dict(criterion_count_by_fact),
        public_order=useful,
        remaining_budget=remaining_budget,
    )


def _run_policy(
    *,
    policy: PolicyName,
    initial_state: PatientState,
    pair: Mapping[str, Any],
    criteria_by_trial: Mapping[str, Sequence[Any]],
    normalized_rows: Sequence[Mapping[str, Any]],
    action_budget: int,
) -> dict[str, Any]:
    patient_id = str(pair["patient_id"])
    descriptions = {
        item["fact_code"]: item["description"] for item in pair["clinical_values"]
    }
    pivotal = list(pair["pivotal_fact_codes"])
    answer_rows = [
        EvidenceFact.model_validate(item)
        for item in pair["insufficient_evidence_episode"]["verification_answers"]
    ]
    answers: dict[str, list[EvidenceFact]] = defaultdict(list)
    for item in answer_rows:
        assert item.concept is not None
        answers[item.concept.rsplit(":", 1)[-1]].append(item)
    state = initial_state
    revealed: list[str] = []
    remaining = list(pivotal)
    target = pair["sufficient_evidence_episode"]["expected_trial_decisions"]
    fixed = _fixed_order(
        group_id=str(pair["group_id"]),
        pivotal_codes=pivotal,
        normalized_rows=normalized_rows,
    )

    def decide() -> list[dict[str, Any]]:
        return _decisions(
            patient_state=state,
            criteria_by_trial=criteria_by_trial,
            requests=_requests(
                patient_id=patient_id,
                pivotal_codes=remaining,
                descriptions=descriptions,
                criteria_by_trial=criteria_by_trial,
            ),
        )

    current = decide()
    trajectory = [{"step": 0, "selected_fact_code": None, **_decision_metrics(current, target)}]
    if policy != "no_questions":
        for step in range(1, action_budget + 1):
            if all(
                item["confirmation_status"]
                in {ConfirmationStatus.CONFIRMED.value, ConfirmationStatus.INELIGIBLE.value}
                for item in current
            ):
                break
            if policy == "fixed_source_order":
                fact_code = next((item for item in fixed if item in remaining), None)
            elif policy == "clarifytrial_rule_v1":
                fact_code = _choose_clarifytrial_fact(
                    remaining_codes=remaining,
                    decisions=current,
                    criteria_by_trial=criteria_by_trial,
                )
            elif policy == "clarifytrial_rule_v2_resolve_first":
                fact_code = _choose_resolve_first_fact(
                    remaining_codes=remaining,
                    decisions=current,
                    criteria_by_trial=criteria_by_trial,
                    patient_state=state,
                )
            else:
                fact_code = _choose_exact_coverage_fact(
                    remaining_codes=remaining,
                    remaining_budget=action_budget - step + 1,
                    decisions=current,
                    criteria_by_trial=criteria_by_trial,
                    patient_state=state,
                )
            if fact_code is None:
                break
            state = _replace_fact_code(state, fact_code, answers[fact_code])
            remaining.remove(fact_code)
            revealed.append(fact_code)
            current = decide()
            trajectory.append(
                {
                    "step": step,
                    "selected_fact_code": fact_code,
                    **_decision_metrics(current, target),
                }
            )
    return {
        "policy_id": policy,
        "selected_fact_codes": revealed,
        "action_count": len(revealed),
        "available_missing_fact_count": len(pivotal),
        "available_missing_fact_recall": len(set(revealed)) / len(pivotal),
        "fixed_source_order": fixed,
        "trajectory": trajectory,
        "final_decisions": current,
        "target_decisions": target,
        "final_metrics": _decision_metrics(current, target),
        "unresolved_to_resolved": (
            trajectory[-1]["resolved_trial_count"]
            - trajectory[0]["resolved_trial_count"]
        ),
    }


def _summaries(runs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[int, str, str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for item in runs:
        groups[
            (
                item["action_budget"],
                item["split"],
                item["input_state"],
                item["policy_id"],
            )
        ].append(item)
    summaries = []
    for (budget, split, input_state, policy), rows in sorted(groups.items()):
        metrics = [item["final_metrics"] for item in rows]
        trial_count = sum(item["trial_count"] for item in metrics)
        summaries.append(
            {
                "action_budget": budget,
                "split": split,
                "input_state": input_state,
                "policy_id": policy,
                "patient_count": len(rows),
                "mean_action_count": sum(item["action_count"] for item in rows) / len(rows),
                "mean_available_missing_fact_recall": sum(
                    item["available_missing_fact_recall"] for item in rows
                ) / len(rows),
                "mean_unresolved_to_resolved": sum(
                    item["unresolved_to_resolved"] for item in rows
                ) / len(rows),
                "trial_status_recovery": sum(item["exact_trial_status_count"] for item in metrics) / trial_count,
                "candidate_status_recovery": sum(item["candidate_status_match_count"] for item in metrics) / trial_count,
                "confirmation_status_recovery": sum(item["confirmation_status_match_count"] for item in metrics) / trial_count,
                "false_candidate_removals": sum(item["false_candidate_removals"] for item in metrics),
                "missed_candidate_removals": sum(item["missed_candidate_removals"] for item in metrics),
                "premature_confirmations": sum(item["premature_confirmations"] for item in metrics),
            }
        )
    return summaries


def _paired_comparisons(runs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_key = {
        (
            item["action_budget"],
            item["split"],
            item["input_state"],
            item["patient_id"],
            item["policy_id"],
        ): item
        for item in runs
    }
    comparisons = []
    compared_policies = (
        "clarifytrial_rule_v1",
        "clarifytrial_rule_v2_resolve_first",
        "clarifytrial_exact_coverage_v3",
    )
    for budget in sorted({item["action_budget"] for item in runs}):
        available_groups = sorted(
            {
                (item["split"], item["input_state"])
                for item in runs
                if item["action_budget"] == budget
            }
        )
        for split, input_state in available_groups:
                patient_ids = sorted(
                    {
                        item["patient_id"]
                        for item in runs
                        if item["action_budget"] == budget
                        and item["split"] == split
                        and item["input_state"] == input_state
                    }
                )
                for compared_policy in compared_policies:
                    deltas = []
                    for patient_id in patient_ids:
                        fixed = by_key[
                            (
                                budget,
                                split,
                                input_state,
                                patient_id,
                                "fixed_source_order",
                            )
                        ]["final_metrics"]["trial_status_recovery"]
                        clarify = by_key[
                            (
                                budget,
                                split,
                                input_state,
                                patient_id,
                                compared_policy,
                            )
                        ]["final_metrics"]["trial_status_recovery"]
                        deltas.append(clarify - fixed)
                    wins = sum(item > 1e-12 for item in deltas)
                    losses = sum(item < -1e-12 for item in deltas)
                    ties = len(deltas) - wins - losses
                    non_ties = wins + losses
                    smaller = min(wins, losses)
                    sign_test = (
                        min(
                            1.0,
                            2
                            * sum(comb(non_ties, index) for index in range(smaller + 1))
                            / (2**non_ties),
                        )
                        if non_ties
                        else 1.0
                    )
                    comparisons.append(
                        {
                            "action_budget": budget,
                            "split": split,
                            "input_state": input_state,
                            "comparison": f"{compared_policy}_minus_fixed_source_order",
                            "patient_count": len(deltas),
                            "mean_trial_status_recovery_delta": sum(deltas) / len(deltas),
                            "clarifytrial_better_patient_count": wins,
                            "equal_patient_count": ties,
                            "clarifytrial_worse_patient_count": losses,
                            "two_sided_exact_sign_test_p": sign_test,
                            "interpretation_limit": (
                                "Paired synthetic result only; ties are excluded from the exact "
                                "sign test and patients are generated from shared templates."
                            ),
                        }
                    )
    return comparisons


def run_natural_policy_evaluation(
    *,
    trial_set_path: str | Path,
    generation_config_path: str | Path,
    patient_pairs_path: str | Path,
    records_path: str | Path,
    structure_result_paths: Sequence[str | Path],
    destination: str | Path,
    action_budget: int = 3,
    action_budgets: Sequence[int] | None = None,
) -> dict[str, Any]:
    if action_budget < 0:
        raise ValueError("action_budget must not be negative")
    budgets = tuple(action_budgets) if action_budgets is not None else (action_budget,)
    if not budgets or any(item < 0 for item in budgets) or len(budgets) != len(set(budgets)):
        raise ValueError("action budgets must be unique non-negative integers")
    trial_set_path = Path(trial_set_path)
    generation_config_path = Path(generation_config_path)
    patient_pairs_path = Path(patient_pairs_path)
    records_path = Path(records_path)
    destination = Path(destination)
    trial_set = json.loads(trial_set_path.read_text(encoding="utf-8"))
    pairs_doc = json.loads(patient_pairs_path.read_text(encoding="utf-8"))
    records_doc = json.loads(records_path.read_text(encoding="utf-8"))
    config = load_natural_patient_generation_config(generation_config_path)
    normalized_rows = _normalized_criteria(trial_set["criteria"], config.fact_aliases)
    criteria_by_group = {
        group.group_id: _trial_criteria(
            [item for item in normalized_rows if item["group_id"] == group.group_id]
        )
        for group in config.groups
    }
    records = {item["episode_id"]: item for item in records_doc["records"]}
    structure_results = {}
    structure_hashes = []
    for path_like in structure_result_paths:
        path = Path(path_like)
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("records_sha256") != portable_text_sha256(records_path):
            raise ValueError(f"structure results do not match natural records: {path}")
        structure_hashes.append(portable_text_sha256(path))
        for item in document["results"]:
            structure_results[item["episode_id"]] = item
    runs = []
    policies: tuple[PolicyName, ...] = (
        "no_questions",
        "fixed_source_order",
        "clarifytrial_rule_v1",
        "clarifytrial_rule_v2_resolve_first",
        "clarifytrial_exact_coverage_v3",
    )
    for pair in pairs_doc["pairs"]:
        episode = pair["insufficient_evidence_episode"]
        episode_id = str(episode["episode_id"])
        record = records[episode_id]
        group_id = str(pair["group_id"])
        criteria_by_trial = criteria_by_group[group_id]
        states = {
            "gold_structured": _gold_state(
                str(pair["patient_id"]), episode, config.as_of
            )
        }
        if episode_id in structure_results:
            states["model_extracted"] = _extracted_state(
                patient_id=str(pair["patient_id"]),
                group_id=group_id,
                record=record,
                result=structure_results[episode_id],
                as_of=config.as_of,
            )
        for budget in budgets:
            for input_state, state in states.items():
                for policy in policies:
                    result = _run_policy(
                        policy=policy,
                        initial_state=state,
                        pair=pair,
                        criteria_by_trial=criteria_by_trial,
                        normalized_rows=normalized_rows,
                        action_budget=budget,
                    )
                    runs.append(
                        {
                            "patient_id": pair["patient_id"],
                            "group_id": group_id,
                            "split": pair["split"],
                            "input_state": input_state,
                            "action_budget": budget,
                            **result,
                        }
                    )
    summaries = _summaries(runs)
    paired_comparisons = _paired_comparisons(runs)
    payload = {
        "protocol_id": "clarifytrial-natural-question-policy-v1",
        "authority": pairs_doc["authority"],
        "medical_data_notice": pairs_doc["medical_data_notice"],
        "medical_disclaimer": pairs_doc["medical_disclaimer"],
        "action_budgets": list(budgets),
        "comparison_rule": (
            "All policies share the same initial record interpretation and hidden "
            "verified answers. Only question permission and question order change."
        ),
        "input_sha256": {
            "trial_set": portable_text_sha256(trial_set_path),
            "generation_config": portable_text_sha256(generation_config_path),
            "patient_pairs": portable_text_sha256(patient_pairs_path),
            "natural_records": portable_text_sha256(records_path),
            "structure_results": structure_hashes,
        },
        "summaries": summaries,
        "paired_comparisons": paired_comparisons,
        "runs": runs,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return {
        "output": str(destination),
        "patient_count": len(pairs_doc["pairs"]),
        "run_count": len(runs),
        "summaries": summaries,
        "paired_comparisons": paired_comparisons,
    }


__all__ = ["run_natural_policy_evaluation"]
