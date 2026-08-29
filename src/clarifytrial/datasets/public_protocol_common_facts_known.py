"""Heldout question-policy evaluation with common facts supplied up front.

This supplemental analysis reuses the frozen public-protocol evaluator so that
only the initial information boundary changes.  Hidden age, pregnancy or
lactation, and active serious infection answers are moved into the initial
patient state and removed from the public question menu before any policy runs.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from math import factorial
from pathlib import Path
from typing import Any

from ..interactive.contracts import InteractiveCase
from ..interactive.statistics import stratified_bootstrap_mean
from .integrity import portable_text_sha256
from .public_protocol_policy_scale import (
    POLICY_IDS,
    _aggregate,
    _budget_auc,
    _fact_code_from_id,
    _flat_auc_comparisons,
    _flat_paired_rows,
    _paired_budget_auc,
    _paired_outputs,
    _policy_rows_for_case,
    _shared_degree,
    _write_csv,
    _write_json,
    load_public_protocol_policy_cases,
)


COMMON_FACT_CODES = (
    "age_years",
    "pregnancy_or_lactation",
    "active_serious_infection",
)
ACTION_BUDGETS = tuple(range(6))
RULE_POLICY_ID = "clarifytrial_rule_v1"
RANDOM_POLICY_ID = "random_order_expectation"
EXACT_POLICY_ID = "clarifytrial_exact_coverage_v3"

QUESTION_CATEGORY_BY_FACT_CODE = {
    "egfr_ml_min_1_73m2": "검사 수치",
    "hemoglobin_g_l": "검사 수치",
    "nct03434392_source_line_39": "과거 수술·치료",
    "nct06033703_source_line_13": "과거 수술·치료",
    "nct07720648_source_line_21": "과거 수술·치료",
    "nct07642414_source_line_7": "증상 빈도·환자 기록",
    "nct06447064_source_line_5": "의료기관 등록 조건",
}


def _question_category(fact_code: str) -> str:
    if fact_code in QUESTION_CATEGORY_BY_FACT_CODE:
        return QUESTION_CATEGORY_BY_FACT_CODE[fact_code]
    if fact_code.startswith("nct"):
        return "진단·병리 조건"
    return "그 밖의 조건"


def _known_case(case_row: Mapping[str, Any], *, action_budget: int) -> dict[str, Any]:
    """Return one validated case after moving every common answer up front."""

    case = case_row["case"]
    common_codes = set(COMMON_FACT_CODES)
    provided = [
        item
        for item in case.hidden_facts
        if _fact_code_from_id(item.request.fact_id) in common_codes
    ]
    remaining = [
        item
        for item in case.hidden_facts
        if _fact_code_from_id(item.request.fact_id) not in common_codes
    ]
    if not remaining:
        raise ValueError(
            f"{case.case_id} has no remaining fact after common facts are supplied"
        )

    provided_evidence_ids = [item.answer.evidence.evidence_id for item in provided]
    known_case = InteractiveCase(
        case_id=case.case_id,
        disease_group=case.disease_group,
        full_patient_state=case.full_patient_state,
        initial_visible_evidence_ids=[
            *case.initial_visible_evidence_ids,
            *provided_evidence_ids,
        ],
        trials=case.trials,
        hidden_facts=remaining,
        action_budget=action_budget,
    )

    provided_fact_ids = [item.request.fact_id for item in provided]
    remaining_fact_ids = [item.request.fact_id for item in remaining]
    menu_fact_ids = [
        item.fact_id for item in known_case.public_policy_view().available_information
    ]
    if menu_fact_ids != remaining_fact_ids:
        raise AssertionError("the public question menu did not match the remaining facts")
    if set(provided_fact_ids) & set(menu_fact_ids):
        raise AssertionError("a preprovided fact remained selectable")
    visible_evidence_ids = {
        item.evidence_id for item in known_case.initial_patient_state().facts
    }
    if not set(provided_evidence_ids).issubset(visible_evidence_ids):
        raise AssertionError("a preprovided answer is absent from the initial state")

    return {
        **case_row,
        "case": known_case,
        "original_hidden_fact_count": len(case.hidden_facts),
        "preprovided_fact_count": len(provided),
        "preprovided_fact_codes": [
            _fact_code_from_id(item) for item in provided_fact_ids
        ],
        "preprovided_fact_ids": provided_fact_ids,
        "preprovided_evidence_ids": provided_evidence_ids,
        "remaining_fact_codes": [
            _fact_code_from_id(item) for item in remaining_fact_ids
        ],
        "remaining_fact_ids": remaining_fact_ids,
        "missing_fact_count": len(remaining),
        **_shared_degree(known_case),
    }


def load_common_facts_known_cases(
    *,
    trial_set_path: str | Path,
    patient_pairs_path: str | Path,
    patient_ids: Sequence[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    """Load the same heldout patients with all declared common facts known."""

    cases, trial_set, pairs_document = load_public_protocol_policy_cases(
        trial_set_path=trial_set_path,
        patient_pairs_path=patient_pairs_path,
        action_budget=max(ACTION_BUDGETS),
        patient_ids=patient_ids,
    )
    heldout = [item for item in cases if item["split"] == "heldout"]
    if not heldout:
        raise ValueError("the selected patient units contain no heldout patients")
    known = [
        _known_case(item, action_budget=max(ACTION_BUDGETS)) for item in heldout
    ]
    return sorted(known, key=lambda item: str(item["patient_id"])), trial_set, pairs_document


def _policy_rows(case_row: Mapping[str, Any], budget: int) -> list[dict[str, Any]]:
    rows = _policy_rows_for_case(case_row, budget)
    allowed = set(case_row["remaining_fact_ids"])
    forbidden = set(case_row["preprovided_fact_ids"])
    result = []
    for row in rows:
        selected = set(row["selected_fact_ids"])
        if selected - allowed:
            raise AssertionError("a policy selected a fact outside the question menu")
        if selected & forbidden:
            raise AssertionError("a policy selected a preprovided fact again")
        result.append(
            {
                **row,
                "original_hidden_fact_count": case_row[
                    "original_hidden_fact_count"
                ],
                "preprovided_fact_count": case_row["preprovided_fact_count"],
                "preprovided_fact_codes": list(
                    case_row["preprovided_fact_codes"]
                ),
                "remaining_fact_codes": list(case_row["remaining_fact_codes"]),
            }
        )
    return result


def _comparison(
    rows: Sequence[Mapping[str, Any]],
    *,
    candidate_policy_id: str,
    baseline_policy_id: str,
    action_budget: int | None = None,
) -> dict[str, Any]:
    selected = [
        item
        for item in rows
        if item["candidate_policy_id"] == candidate_policy_id
        and item["baseline_policy_id"] == baseline_policy_id
        and (
            action_budget is None
            or int(item.get("action_budget", -1)) == action_budget
        )
    ]
    if len(selected) != 1:
        raise AssertionError(
            "expected exactly one paired comparison for "
            f"{candidate_policy_id} versus {baseline_policy_id}"
        )
    return dict(selected[0])


def _exact_rule_equivalence(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Compare exact coverage and the rule on every patient-budget unit."""

    by_key = {
        (
            str(item["patient_id"]),
            int(item["action_budget"]),
            str(item["policy_id"]),
        ): item
        for item in rows
    }
    patient_ids = sorted({str(item["patient_id"]) for item in rows})
    budgets = sorted({int(item["action_budget"]) for item in rows})
    metrics = (
        "trial_status_recovery",
        "candidate_status_recovery",
        "confirmation_status_recovery",
        "confirmed_rescue_count",
        "ineligible_cleanup_count",
        "unsafe_decision_count",
        "action_count",
    )
    comparison_rows = []
    for patient_id in patient_ids:
        for budget in budgets:
            exact = by_key[(patient_id, budget, EXACT_POLICY_ID)]
            rule = by_key[(patient_id, budget, RULE_POLICY_ID)]
            differences = {
                f"{metric}_difference": float(exact[metric]) - float(rule[metric])
                for metric in metrics
            }
            comparison_rows.append(
                {
                    "patient_id": patient_id,
                    "group_id": exact["group_id"],
                    "disease_group": exact["disease_group"],
                    "action_budget": budget,
                    "selected_fact_ids_match": (
                        exact["selected_fact_ids"] == rule["selected_fact_ids"]
                    ),
                    **differences,
                }
            )

    mismatch_rows = [
        item
        for item in comparison_rows
        if any(
            abs(float(item[f"{metric}_difference"])) > 1e-12
            for metric in metrics
        )
    ]
    return (
        {
            "patient_budget_unit_count": len(comparison_rows),
            "outcome_metric_names": list(metrics),
            "all_outcome_metrics_equal": not mismatch_rows,
            "outcome_metric_mismatch_unit_count": len(mismatch_rows),
            "maximum_absolute_metric_difference": max(
                (
                    abs(float(item[f"{metric}_difference"]))
                    for item in comparison_rows
                    for metric in metrics
                ),
                default=0.0,
            ),
            "selected_fact_sequences_all_equal": all(
                bool(item["selected_fact_ids_match"])
                for item in comparison_rows
            ),
            "selected_fact_sequence_mismatch_unit_count": sum(
                not bool(item["selected_fact_ids_match"])
                for item in comparison_rows
            ),
        },
        comparison_rows,
    )


def _csv_ready(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            key: (
                json.dumps(value, ensure_ascii=False)
                if isinstance(value, (list, dict))
                else value
            )
            for key, value in item.items()
        }
        for item in rows
    ]


def _direct_transition_summary(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[
    dict[str, Any],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Count observable pending-to-decision transitions after one question."""

    initial = {
        str(item["patient_id"]): item
        for item in rows
        if int(item["action_budget"]) == 0
        and item["policy_id"] == "no_questions"
    }
    after = {
        str(item["patient_id"]): item
        for item in rows
        if int(item["action_budget"]) == 1
        and item["policy_id"] == RULE_POLICY_ID
    }
    if set(initial) != set(after):
        raise AssertionError("B0 and B1 patient units do not match")

    patient_rows = []
    status_rate_differences: dict[str, list[float]] = defaultdict(list)
    newly_matched_differences: dict[str, list[float]] = defaultdict(list)
    for patient_id in sorted(initial):
        before = initial[patient_id]
        after_one = after[patient_id]
        group_id = str(after_one["group_id"])
        newly_matched = float(after_one["trial_status_match_count"]) - float(
            before["trial_status_match_count"]
        )
        status_rate_difference = float(
            after_one["trial_status_recovery"]
        ) - float(before["trial_status_recovery"])
        status_rate_differences[group_id].append(status_rate_difference)
        newly_matched_differences[group_id].append(newly_matched)
        patient_rows.append(
            {
                "patient_id": patient_id,
                "group_id": group_id,
                "disease_group": after_one["disease_group"],
                "trial_count": int(float(before["trial_count"])),
                "initial_unresolved_trial_count": int(
                    float(before["rescue_opportunity_count"])
                    + float(before["cleanup_opportunity_count"])
                ),
                "question_count": int(float(after_one["action_count"])),
                "newly_matched_trial_count": newly_matched,
                "trial_status_match_rate_difference": status_rate_difference,
                "confirmed_after_one_question_count": int(
                    float(after_one["confirmed_rescue_count"])
                ),
                "excluded_after_one_question_count": int(
                    float(after_one["ineligible_cleanup_count"])
                ),
                "unsafe_new_decision_count": int(
                    float(after_one["unsafe_decision_count"])
                ),
            }
        )

    patient_inference = {
        "trial_status_match_rate_difference": stratified_bootstrap_mean(
            status_rate_differences,
            cluster_unit="base_patient",
        ),
        "newly_matched_trial_count": stratified_bootstrap_mean(
            newly_matched_differences,
            cluster_unit="base_patient",
        ),
    }

    initial_unresolved = sum(
        float(item["rescue_opportunity_count"])
        + float(item["cleanup_opportunity_count"])
        for item in initial.values()
    )
    confirmed = sum(
        float(item["confirmed_rescue_count"]) for item in after.values()
    )
    excluded = sum(
        float(item["ineligible_cleanup_count"]) for item in after.values()
    )
    resolved = confirmed + excluded
    selected_codes = [
        _fact_code_from_id(str(fact_id))
        for item in after.values()
        for fact_id in item["selected_fact_ids"]
    ]
    category_counts: dict[str, int] = {}
    for fact_code in selected_codes:
        category = _question_category(fact_code)
        category_counts[category] = category_counts.get(category, 0) + 1
    category_rows = [
        {
            "question_category": category,
            "question_count": count,
            "share_of_questions": count / len(selected_codes),
        }
        for category, count in sorted(category_counts.items())
    ]
    patient_count = len(after)
    patients_with_resolution = sum(
        float(item["confirmed_rescue_count"])
        + float(item["ineligible_cleanup_count"])
        > 0
        for item in after.values()
    )
    all_group_ids = {str(item["group_id"]) for item in after.values()}
    resolution_group_ids = {
        str(item["group_id"])
        for item in after.values()
        if float(item["confirmed_rescue_count"])
        + float(item["ineligible_cleanup_count"])
        > 0
    }
    patients_asked = sum(float(item["action_count"]) > 0 for item in after.values())
    unsafe = sum(float(item["unsafe_decision_count"]) for item in after.values())
    return (
        {
            "patient_count": patient_count,
            "trial_pair_count": int(
                sum(float(item["trial_count"]) for item in initial.values())
            ),
            "initial_unresolved_trial_count": int(initial_unresolved),
            "resolved_after_one_question_count": int(resolved),
            "resolved_after_one_question_rate": (
                resolved / initial_unresolved if initial_unresolved else 0.0
            ),
            "confirmed_after_one_question_count": int(confirmed),
            "excluded_after_one_question_count": int(excluded),
            "patients_asked_count": patients_asked,
            "patients_with_at_least_one_resolution_count": (
                patients_with_resolution
            ),
            "disease_group_count": len(all_group_ids),
            "disease_groups_with_at_least_one_resolution_count": len(
                resolution_group_ids
            ),
            "disease_group_ids_with_at_least_one_resolution": sorted(
                resolution_group_ids
            ),
            "unsafe_new_decision_count": int(unsafe),
            "question_count": len(selected_codes),
            "question_fact_codes": sorted(selected_codes),
            "question_category_counts": category_counts,
            "duplicated_age_question_count": category_counts.get(
                "나이 조건 중복", 0
            ),
            "independent_inference_unit": "base_patient",
            "trial_pairs_per_patient": 5,
            "conditional_denominator_definition": (
                "Trial pairs still unresolved after age, pregnancy or lactation, "
                "and active serious infection were supplied at the start."
            ),
            "paired_patient_inference": patient_inference,
            "interpretation_boundary": (
                "같은 합성 환자에서 질문 전후 상태 변화를 비교했다. 14/22는 세 기본 "
                "항목을 먼저 제공하고도 남은 22건에서 질문, 답 반영과 재판정이 이어지는지 "
                "본 값이다. 숫자 범위를 계산할 때는 환자 한 명을 한 단위로 세었고, 같은 "
                "환자에게 연결한 시험 다섯 건을 환자 다섯 명처럼 세지 않았다. 실제 임상 "
                "결과나 참가 판정 정확도가 아니다."
            ),
        },
        category_rows,
        patient_rows,
    )


def run_public_protocol_common_facts_known(
    *,
    trial_set_path: str | Path,
    patient_pairs_path: str | Path,
    output_dir: str | Path,
    patient_ids: Sequence[str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> Path:
    """Run budgets 0..5 and write deterministic supplemental result tables."""

    cases, trial_set, pairs_document = load_common_facts_known_cases(
        trial_set_path=trial_set_path,
        patient_pairs_path=patient_pairs_path,
        patient_ids=patient_ids,
    )
    rows = []
    for index, case_row in enumerate(cases, start=1):
        for budget in ACTION_BUDGETS:
            rows.extend(_policy_rows(case_row, budget))
        if progress is not None and (index % 5 == 0 or index == len(cases)):
            progress(f"completed {index}/{len(cases)} heldout patients")

    policy_metrics = _aggregate(rows, ("split", "action_budget", "policy_id"))
    budget_auc = _budget_auc(policy_metrics, ACTION_BUDGETS)
    patient_differences, paired_comparisons = _paired_outputs(rows, ACTION_BUDGETS)
    auc_patient_rows, paired_auc = _paired_budget_auc(rows, ACTION_BUDGETS)

    budget_one_rule_random = _comparison(
        paired_comparisons,
        candidate_policy_id=RULE_POLICY_ID,
        baseline_policy_id=RANDOM_POLICY_ID,
        action_budget=1,
    )
    budget_one_exact_rule = _comparison(
        paired_comparisons,
        candidate_policy_id=EXACT_POLICY_ID,
        baseline_policy_id=RULE_POLICY_ID,
        action_budget=1,
    )
    auc_rule_random = _comparison(
        paired_auc,
        candidate_policy_id=RULE_POLICY_ID,
        baseline_policy_id=RANDOM_POLICY_ID,
    )
    auc_exact_rule = _comparison(
        paired_auc,
        candidate_policy_id=EXACT_POLICY_ID,
        baseline_policy_id=RULE_POLICY_ID,
    )
    exact_rule_equivalence, exact_rule_rows = _exact_rule_equivalence(rows)
    (
        direct_transition,
        question_category_rows,
        direct_transition_patient_rows,
    ) = _direct_transition_summary(rows)

    requested_pair_ids = {
        (RULE_POLICY_ID, RANDOM_POLICY_ID),
        (EXACT_POLICY_ID, RULE_POLICY_ID),
    }
    requested_budget_one_differences = [
        item
        for item in patient_differences
        if int(item["action_budget"]) == 1
        and (
            str(item["candidate_policy_id"]),
            str(item["baseline_policy_id"]),
        )
        in requested_pair_ids
    ]
    requested_budget_one_comparisons = [
        budget_one_rule_random,
        budget_one_exact_rule,
    ]
    requested_auc_patient_rows = [
        item
        for item in auc_patient_rows
        if (
            str(item["candidate_policy_id"]),
            str(item["baseline_policy_id"]),
        )
        in requested_pair_ids
    ]
    requested_auc_comparisons = [auc_rule_random, auc_exact_rule]

    original_counts = sorted(
        {int(item["original_hidden_fact_count"]) for item in cases}
    )
    remaining_counts = sorted({int(item["missing_fact_count"]) for item in cases})
    maximum_remaining = max(remaining_counts)
    summary = {
        "protocol_id": "clarifytrial-public-protocol-common-facts-known-v1",
        "source_protocol_id": trial_set["protocol_id"],
        "evaluation_scope": (
            "Deterministic heldout sensitivity on selected structured public trial "
            "criteria and synthetic patients; not clinical performance."
        ),
        "patient_unit": "heldout_synthetic_patient",
        "common_fact_codes_preprovided": list(COMMON_FACT_CODES),
        "preprovided_rule": (
            "Every hidden fact with one of the declared common codes is copied "
            "into the initial patient state before policy evaluation."
        ),
        "question_menu_rule": (
            "Preprovided facts are removed from available_information and cannot "
            "be selected again."
        ),
        "action_budgets": list(ACTION_BUDGETS),
        "policy_ids": list(POLICY_IDS),
        "heldout_patient_count": len(cases),
        "disease_group_count": len({str(item["group_id"]) for item in cases}),
        "original_hidden_fact_count_distribution": {
            str(value): sum(
                int(item["original_hidden_fact_count"]) == value for item in cases
            )
            for value in original_counts
        },
        "preprovided_fact_count_distribution": {
            str(value): sum(
                int(item["preprovided_fact_count"]) == value for item in cases
            )
            for value in sorted(
                {int(item["preprovided_fact_count"]) for item in cases}
            )
        },
        "remaining_hidden_fact_count_distribution": {
            str(value): sum(
                int(item["missing_fact_count"]) == value for item in cases
            )
            for value in remaining_counts
        },
        "random_order_rule": (
            "Exact arithmetic mean over all permutations of the remaining fact "
            f"menu; the current maximum is {maximum_remaining}! = "
            f"{factorial(maximum_remaining)} orders per patient-budget unit."
        ),
        "patient_is_independent_unit": True,
        "repeated_unit_warning": (
            "Budgets and policies are repeated evaluations of the same patient, "
            "not additional independent patients."
        ),
        "bootstrap_rule": (
            "Paired patient differences resampled within disease groups with "
            "5,000 deterministic draws."
        ),
        "primary_comparisons": {
            "budget_1_rule_minus_random": budget_one_rule_random,
            "normalized_auc_0_5_rule_minus_random": auc_rule_random,
            "budget_1_exact_minus_rule": budget_one_exact_rule,
            "normalized_auc_0_5_exact_minus_rule": auc_exact_rule,
        },
        "exact_rule_equivalence": exact_rule_equivalence,
        "direct_budget_0_to_1_transition": direct_transition,
        "policy_metrics": policy_metrics,
        "budget_auc": budget_auc,
        "input_paths": {
            "trial_set": str(trial_set_path),
            "patient_pairs": str(patient_pairs_path),
        },
        "input_sha256": {
            "trial_set": portable_text_sha256(Path(trial_set_path)),
            "patient_pairs": portable_text_sha256(Path(patient_pairs_path)),
        },
        "model_calls": 0,
        "model_tokens": 0,
        "runtime_measurement": (
            "Excluded from the deterministic result files; measure wall time "
            "around the runner command."
        ),
        "medical_data_notice": pairs_document["medical_data_notice"],
        "medical_disclaimer": pairs_document["medical_disclaimer"],
    }

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    _write_json(destination / "summary.json", summary)
    _write_json(destination / "patient-results.json", rows)
    _write_csv(destination / "patient-results.csv", _csv_ready(rows))
    _write_csv(destination / "policy-metrics.csv", policy_metrics)
    _write_csv(destination / "budget-auc.csv", budget_auc)
    _write_csv(
        destination / "budget-1-paired-patient-differences.csv",
        requested_budget_one_differences,
    )
    _write_csv(
        destination / "budget-1-paired-comparisons.csv",
        _flat_paired_rows(requested_budget_one_comparisons),
    )
    _write_csv(
        destination / "paired-auc-patient-differences.csv",
        requested_auc_patient_rows,
    )
    _write_csv(
        destination / "paired-auc-comparisons.csv",
        _flat_auc_comparisons(requested_auc_comparisons),
    )
    _write_csv(destination / "exact-rule-comparison.csv", exact_rule_rows)
    _write_csv(
        destination / "direct-transition-summary.csv",
        _csv_ready([direct_transition]),
    )
    _write_csv(
        destination / "question-category-counts.csv",
        question_category_rows,
    )
    _write_csv(
        destination / "direct-transition-patient-differences.csv",
        direct_transition_patient_rows,
    )
    return destination / "summary.json"


__all__ = [
    "ACTION_BUDGETS",
    "COMMON_FACT_CODES",
    "load_common_facts_known_cases",
    "run_public_protocol_common_facts_known",
]
