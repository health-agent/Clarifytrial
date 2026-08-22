"""Compare question policies on frozen standardized synthetic patient pairs.

Natural-record extraction results may replace the standardized input for an
optional connection test. The policy comparison itself only changes whether
questions are allowed and how the next fact is selected. Verified answers come
from the hidden sufficient member of each synthetic pair.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from itertools import combinations
from math import comb
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
                reason="참가 조건을 확인할 자료가 아직 충분하지 않다.",
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


def _without_fact_codes(
    state: PatientState, fact_codes: Sequence[str]
) -> PatientState:
    """Return the same patient state with the named structured facts removed."""

    removed = set(fact_codes)
    return state.model_copy(
        update={
            "facts": [
                item
                for item in state.facts
                if item.concept is None
                or item.concept.rsplit(":", 1)[-1] not in removed
            ]
        }
    )


def _decisions_for_state(
    *,
    state: PatientState,
    remaining_codes: Sequence[str],
    patient_id: str,
    descriptions: Mapping[str, str],
    criteria_by_trial: Mapping[str, Sequence[Any]],
) -> list[dict[str, Any]]:
    return _decisions(
        patient_state=state,
        criteria_by_trial=criteria_by_trial,
        requests=_requests(
            patient_id=patient_id,
            pivotal_codes=remaining_codes,
            descriptions=descriptions,
            criteria_by_trial=criteria_by_trial,
        ),
    )


def _question_selection_reference(
    *,
    initial_state: PatientState,
    pivotal_codes: Sequence[str],
    answers: Mapping[str, Sequence[EvidenceFact]],
    patient_id: str,
    descriptions: Mapping[str, str],
    criteria_by_trial: Mapping[str, Sequence[Any]],
    target: Sequence[Mapping[str, Any]],
    action_budget: int,
) -> dict[str, Any]:
    """Find the smallest best question sets under the same action budget.

    This reference is calculated only after the hidden synthetic answers are
    available for scoring.  It is never passed to the question policy.
    """

    max_questions = min(action_budget, len(pivotal_codes))
    scored_sets: list[tuple[frozenset[str], int]] = []
    for count in range(max_questions + 1):
        for chosen_tuple in combinations(pivotal_codes, count):
            chosen = frozenset(chosen_tuple)
            state = initial_state
            for fact_code in chosen_tuple:
                state = _replace_fact_code(state, fact_code, answers[fact_code])
            remaining = [item for item in pivotal_codes if item not in chosen]
            decisions = _decisions_for_state(
                state=state,
                remaining_codes=remaining,
                patient_id=patient_id,
                descriptions=descriptions,
                criteria_by_trial=criteria_by_trial,
            )
            score = _decision_metrics(decisions, target)["exact_trial_status_count"]
            scored_sets.append((chosen, score))

    best_score = max(score for _, score in scored_sets)
    best_sets = [chosen for chosen, score in scored_sets if score == best_score]
    smallest_size = min(len(chosen) for chosen in best_sets)
    minimal_best_sets = [
        chosen for chosen in best_sets if len(chosen) == smallest_size
    ]
    ordered_sets = sorted(
        (sorted(chosen) for chosen in minimal_best_sets),
        key=lambda chosen: (len(chosen), chosen),
    )
    return {
        "best_trial_status_recovery_within_budget": (
            best_score / len(target)
        ),
        "smallest_best_question_count": min(map(len, ordered_sets)),
        "smallest_best_question_sets": ordered_sets,
    }


def _question_selection_metrics(
    *,
    selected_fact_codes: Sequence[str],
    final_trial_status_recovery: float,
    reference: Mapping[str, Any],
) -> dict[str, Any]:
    """Score selected questions against all equally good minimal choices."""

    selected = set(selected_fact_codes)
    acceptable = [set(items) for items in reference["smallest_best_question_sets"]]
    scored_choices = [
        (
            len(selected & needed) / len(needed) if needed else 1.0,
            len(selected - needed),
        )
        for needed in acceptable
    ]
    selected_recall, selected_extras = max(
        scored_choices,
        key=lambda item: (item[0], -item[1]),
    )
    best_recovery = float(reference["best_trial_status_recovery_within_budget"])
    return {
        "needed_fact_recall": selected_recall,
        "unnecessary_action_count": selected_extras,
        "best_trial_status_recovery_within_budget": best_recovery,
        "trial_status_recovery_gap_from_best": max(
            0.0,
            best_recovery - final_trial_status_recovery,
        ),
        "smallest_best_question_count": reference[
            "smallest_best_question_count"
        ],
        "smallest_best_question_sets": reference[
            "smallest_best_question_sets"
        ],
    }


def _decision_changes(
    before: Sequence[Mapping[str, Any]],
    after: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    before_by_trial = {str(item["trial_id"]): item for item in before}
    changes = []
    for current in after:
        trial_id = str(current["trial_id"])
        previous = before_by_trial[trial_id]
        old_status = (
            previous["candidate_status"],
            previous["confirmation_status"],
        )
        new_status = (
            current["candidate_status"],
            current["confirmation_status"],
        )
        if old_status != new_status:
            changes.append(
                {
                    "trial_id": trial_id,
                    "before_candidate_status": old_status[0],
                    "before_confirmation_status": old_status[1],
                    "after_candidate_status": new_status[0],
                    "after_confirmation_status": new_status[1],
                }
            )
    return changes


def _selection_reason(policy: PolicyName) -> str:
    return {
        "no_questions": "질문하지 않는 비교 조건이다.",
        "fixed_source_order": "임상시험 기준이 저장된 순서대로 확인한다.",
        "clarifytrial_rule_v1": "현재 미정인 시험과 가장 많이 연결된 정보를 고른다.",
        "clarifytrial_rule_v2_resolve_first": (
            "한 번의 확인으로 판정을 끝낼 수 있는 시험을 먼저 찾는다."
        ),
        "clarifytrial_exact_coverage_v3": (
            "남은 질문 횟수 안에서 가장 많은 시험 판정을 끝낼 수 있는 "
            "질문 묶음을 계산해 고른다."
        ),
    }[policy]


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
    question_reference: Mapping[str, Any] | None = None,
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
        return _decisions_for_state(
            state=state,
            remaining_codes=remaining,
            patient_id=patient_id,
            descriptions=descriptions,
            criteria_by_trial=criteria_by_trial,
        )

    if question_reference is None:
        question_reference = _question_selection_reference(
            initial_state=initial_state,
            pivotal_codes=pivotal,
            answers=answers,
            patient_id=patient_id,
            descriptions=descriptions,
            criteria_by_trial=criteria_by_trial,
            target=target,
            action_budget=action_budget,
        )
    current = decide()
    trajectory = [
        {
            "step": 0,
            "selected_fact_code": None,
            "decisions": current,
            **_decision_metrics(current, target),
        }
    ]
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
            previous = current
            route, acceptable_actions = _fact_route(fact_code)
            related_trial_ids = sorted(
                trial_id
                for trial_id, criteria in criteria_by_trial.items()
                if any(
                    criterion.numeric_constraint is not None
                    and criterion.numeric_constraint.concept.rsplit(":", 1)[-1]
                    == fact_code
                    for criterion in criteria
                )
            )
            state = _replace_fact_code(state, fact_code, answers[fact_code])
            remaining.remove(fact_code)
            revealed.append(fact_code)
            current = decide()
            trajectory.append(
                {
                    "step": step,
                    "selected_fact_code": fact_code,
                    "fact_description": descriptions[fact_code],
                    "question": f"확인 항목: {descriptions[fact_code]}",
                    "selected_action": route.value,
                    "acceptable_actions": [
                        item.value for item in acceptable_actions
                    ],
                    "selection_reason": _selection_reason(policy),
                    "related_trial_ids": related_trial_ids,
                    "synthetic_answer_evidence": [
                        item.model_dump(mode="json") for item in answers[fact_code]
                    ],
                    "decision_changes": _decision_changes(previous, current),
                    "decisions": current,
                    **_decision_metrics(current, target),
                }
            )
    final_metrics = _decision_metrics(current, target)
    initial_pivotal_codes = {
        item.concept.rsplit(":", 1)[-1]
        for item in initial_state.facts
        if item.concept is not None
        and item.concept.rsplit(":", 1)[-1] in pivotal
    }
    return {
        "policy_id": policy,
        "initial_fact_count": len(initial_state.facts),
        "initial_pivotal_fact_count": len(initial_pivotal_codes),
        "selected_fact_codes": revealed,
        "action_count": len(revealed),
        "available_missing_fact_count": len(pivotal),
        "available_missing_fact_recall": len(set(revealed)) / len(pivotal),
        "fixed_source_order": fixed,
        "trajectory": trajectory,
        "final_decisions": current,
        "target_decisions": target,
        "final_metrics": final_metrics,
        "question_selection_metrics": _question_selection_metrics(
            selected_fact_codes=revealed,
            final_trial_status_recovery=final_metrics["trial_status_recovery"],
            reference=question_reference,
        ),
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
                "mean_needed_fact_recall": sum(
                    item["question_selection_metrics"]["needed_fact_recall"]
                    for item in rows
                ) / len(rows),
                "mean_unnecessary_action_count": sum(
                    item["question_selection_metrics"][
                        "unnecessary_action_count"
                    ]
                    for item in rows
                ) / len(rows),
                "mean_best_trial_status_recovery_within_budget": sum(
                    item["question_selection_metrics"][
                        "best_trial_status_recovery_within_budget"
                    ]
                    for item in rows
                ) / len(rows),
                "mean_trial_status_recovery_gap_from_best": sum(
                    item["question_selection_metrics"][
                        "trial_status_recovery_gap_from_best"
                    ]
                    for item in rows
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
    splits: Sequence[str] | None = None,
    patient_ids: Sequence[str] | None = None,
    include_fully_missing: bool = False,
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
    selected_splits = set(splits or ())
    allowed_splits = {"development", "heldout"}
    if unknown_splits := selected_splits - allowed_splits:
        raise ValueError(f"unknown splits: {sorted(unknown_splits)!r}")
    selected_patient_ids = set(patient_ids or ())
    available_patient_ids = {str(item["patient_id"]) for item in pairs_doc["pairs"]}
    if unknown_patient_ids := selected_patient_ids - available_patient_ids:
        raise ValueError(
            f"unknown patient IDs: {sorted(unknown_patient_ids)!r}"
        )
    selected_pairs = [
        item
        for item in pairs_doc["pairs"]
        if (not selected_splits or str(item["split"]) in selected_splits)
        and (
            not selected_patient_ids
            or str(item["patient_id"]) in selected_patient_ids
        )
    ]
    if not selected_pairs:
        raise ValueError("the requested JSON evaluation filter selected no patients")
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
    for pair in selected_pairs:
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
        if include_fully_missing:
            states["fully_missing"] = _without_fact_codes(
                states["gold_structured"], pair["pivotal_fact_codes"]
            )
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
                descriptions = {
                    item["fact_code"]: item["description"]
                    for item in pair["clinical_values"]
                }
                answer_map: dict[str, list[EvidenceFact]] = defaultdict(list)
                for answer_row in episode["verification_answers"]:
                    answer = EvidenceFact.model_validate(answer_row)
                    assert answer.concept is not None
                    answer_map[answer.concept.rsplit(":", 1)[-1]].append(answer)
                question_reference = _question_selection_reference(
                    initial_state=state,
                    pivotal_codes=pair["pivotal_fact_codes"],
                    answers=answer_map,
                    patient_id=str(pair["patient_id"]),
                    descriptions=descriptions,
                    criteria_by_trial=criteria_by_trial,
                    target=pair["sufficient_evidence_episode"][
                        "expected_trial_decisions"
                    ],
                    action_budget=budget,
                )
                for policy in policies:
                    result = _run_policy(
                        policy=policy,
                        initial_state=state,
                        pair=pair,
                        criteria_by_trial=criteria_by_trial,
                        normalized_rows=normalized_rows,
                        action_budget=budget,
                        question_reference=question_reference,
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
        "question_metric_rule": (
            "After each run, every question subset allowed by the same action budget "
            "is scored with hidden synthetic answers. Needed-fact recall and "
            "unnecessary actions compare the selected questions with all smallest "
            "question sets that achieve the best result within that budget. This "
            "reference is not visible to the policy."
        ),
        "input_mode": (
            "standardized_json_and_natural_record_extraction"
            if structure_result_paths
            else "standardized_json"
        ),
        "filters": {
            "splits": sorted(selected_splits),
            "patient_ids": sorted(selected_patient_ids),
            "include_fully_missing": include_fully_missing,
        },
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
        "patient_count": len(selected_pairs),
        "run_count": len(runs),
        "summaries": summaries,
        "paired_comparisons": paired_comparisons,
    }


__all__ = ["run_natural_policy_evaluation"]
