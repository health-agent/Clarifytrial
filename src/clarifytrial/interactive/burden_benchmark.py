"""Run and report the patient-specific acquisition-burden benchmark."""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from statistics import mean
from typing import Any

from ..contracts import CandidateStatus, ConfirmationStatus
from ..reporting import build_recommendation_views
from .burden_contracts import (
    AcquisitionDecision,
    AcquisitionMode,
    AcquisitionOption,
    AcquisitionPolicyId,
    ActionStatus,
    AvailabilityStructure,
    BurdenActionRecord,
    BurdenPolicyRun,
    BurdenRunMetrics,
    DetailedAlternative,
    DetailedSelectedOption,
    DirectCostBand,
    GuidanceOutput,
    OutcomePreview,
    OutcomePreviewBranch,
    PatientBurdenProfile,
    PatientGuidance,
    PatientInputStatus,
    TrialGroups,
)
from .burden_policy import (
    benchmark_patient_profiles,
    build_acquisition_catalog,
    current_fact_impacts,
    explicit_limit_violations,
    option_cost_rank,
    option_is_dominated,
    select_acquisition_option,
)
from .contracts import InteractiveCase, InteractivePolicyView, InteractiveSnapshot
from .oracle import evaluate_interactive_case
from .public_benchmark import (
    audit_public_sources,
    build_public_case,
    load_public_benchmark_spec,
)


_MEDICAL_DISCLAIMER = (
    "ClarifyTrial은 연구용 시제품입니다. 이 결과만으로 임상시험 참가 가능성을 "
    "확정할 수 없습니다. 자격을 판단할 때는 의료 전문가와 해당 임상시험 연구진이 "
    "최신 공식 계획서와 전체 환자 기록을 다시 확인해야 합니다."
)
_POLICY_IDS = tuple(AcquisitionPolicyId)
_AVAILABILITY_STRUCTURES = tuple(AvailabilityStructure)
_COST_LABELS = {
    DirectCostBand.NONE: "추가 비용 없음",
    DirectCostBand.LOW: "낮은 비용 범주",
    DirectCostBand.MEDIUM: "중간 비용 범주",
    DirectCostBand.HIGH: "높은 비용 범주",
    DirectCostBand.UNKNOWN: "비용 미확인",
}
_MODE_LABELS = {
    AcquisitionMode.INTERNAL_RECORD: "병원 내부 기록 확인",
    AcquisitionMode.OUTSIDE_RECORD: "다른 기관의 기존 기록 요청",
    AcquisitionMode.PATIENT_REPORT: "환자에게 직접 확인",
    AcquisitionMode.EXISTING_OFFICIAL_RESULT: "이미 받은 공식 결과 확인",
    AcquisitionMode.NEW_NONINVASIVE_TEST: "새 비침습 검사 검토",
    AcquisitionMode.NEW_INVASIVE_OR_TREATMENT_CHANGE: "침습 절차 또는 치료 변경 검토",
    AcquisitionMode.CLINICIAN_JUDGMENT: "의료진의 별도 평가",
}
_SETTING_LABELS = {
    "time_urgency_0_to_3": "확인 시간의 긴급성",
    "fatigue_or_mobility_limit_0_to_3": "피로·이동 어려움",
    "travel_constraint_0_to_3": "이동 거리 부담",
    "cost_sensitivity_0_to_3": "추가 비용 걱정",
    "procedure_aversion_0_to_3": "검사·절차 부담",
    "treatment_change_aversion_0_to_3": "치료 변경 부담",
    "preference_mode": "확인 순서 선택 방식",
    "stated_limits": "환자가 직접 밝힌 한도",
}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def trial_groups(snapshot: InteractiveSnapshot) -> TrialGroups:
    confirmed = []
    pending = []
    removed = []
    for decision in snapshot.decisions:
        if (
            decision.candidate_status is CandidateStatus.REMOVE
            or decision.confirmation_status is ConfirmationStatus.INELIGIBLE
        ):
            removed.append(decision.trial_id)
        elif (
            decision.candidate_status is CandidateStatus.RETAIN
            and decision.confirmation_status is ConfirmationStatus.CONFIRMED
        ):
            confirmed.append(decision.trial_id)
        else:
            pending.append(decision.trial_id)
    return TrialGroups(
        confirmed_trial_ids=sorted(confirmed),
        pending_trial_ids=sorted(pending),
        removed_trial_ids=sorted(removed),
    )


def _existing_or_new(option: AcquisitionOption | None) -> str:
    if option is None:
        return "확인 경로 미정"
    if option.acquisition_mode in {
        AcquisitionMode.INTERNAL_RECORD,
        AcquisitionMode.OUTSIDE_RECORD,
        AcquisitionMode.EXISTING_OFFICIAL_RESULT,
    }:
        return "기존 자료 확인"
    if option.acquisition_mode is AcquisitionMode.PATIENT_REPORT:
        return "환자 답변"
    if option.acquisition_mode in {
        AcquisitionMode.NEW_NONINVASIVE_TEST,
        AcquisitionMode.NEW_INVASIVE_OR_TREATMENT_CHANGE,
    }:
        return "새 검사·절차 가능성"
    return "의료진 별도 평가"


def _level_label(value: int | None) -> str:
    return {None: "미확인", 0: "거의 없음", 1: "낮음", 2: "중간", 3: "큼"}[value]


def _burden_lines(option: AcquisitionOption | None) -> list[str]:
    if option is None:
        return ["선택할 경로가 없어 추가 부담을 계산하지 않음"]
    delay = (
        "대기 시간 미확인"
        if option.expected_delay_hours is None
        else f"예상 대기 {option.expected_delay_hours:g}시간"
    )
    visit = (
        "추가 방문 미확인"
        if option.visit_required is None
        else ("추가 방문 필요" if option.visit_required else "추가 방문 없음")
    )
    return [
        delay,
        visit,
        _COST_LABELS[option.direct_cost_band],
        f"신체 부담 {_level_label(option.physical_burden_0_to_3)}",
        f"심리 부담 {_level_label(option.emotional_burden_0_to_3)}",
        f"의료 위험 {_level_label(option.medical_risk_0_to_3)}",
        f"기존 치료 방해 {_level_label(option.treatment_disruption_0_to_3)}",
    ]


def _profile_lines(profile: PatientBurdenProfile) -> list[str]:
    status = {
        PatientInputStatus.ABSENT: "환자 부담 입력 없음",
        PatientInputStatus.PARTIAL: "환자 부담 일부 입력",
        PatientInputStatus.COMPLETE: "합성 평가용 부담 상황 전체 입력",
    }[profile.input_status]
    mode = {
        "balanced": "빠른 확인과 낮은 부담을 함께 살핌",
        "fastest": "기다리는 시간을 먼저 줄임",
        "least_extra_burden": "새로 생기는 부담을 먼저 줄임",
    }[profile.preference_mode.value]
    result = [status, mode]
    if profile.defaulted_fields:
        labels = [_SETTING_LABELS[item] for item in profile.defaulted_fields]
        result.append("입력이 없어 기본 규칙을 사용: " + ", ".join(labels))
    else:
        result.append("입력한 부담 상황을 사용")
    return result


def _outcome_preview(affected_trial_ids: Sequence[str]) -> OutcomePreview:
    ids = list(affected_trial_ids)
    return OutcomePreview(
        if_satisfies=OutcomePreviewBranch(
            affected_trial_ids=ids,
            message="조건을 충족하면 이 시험들의 확인 대기 항목이 줄거나 현재 확인 후보가 될 수 있다.",
        ),
        if_violates=OutcomePreviewBranch(
            affected_trial_ids=ids,
            message="조건을 충족하지 않으면 이 시험들 가운데 일부가 현재 제외될 수 있다.",
        ),
        if_unavailable=OutcomePreviewBranch(
            affected_trial_ids=ids,
            message="정보를 얻지 못하면 현재 후보 상태를 유지하고 확인 대기로 남긴다.",
        ),
    )


def _difference(selected: AcquisitionOption | None, alternative: AcquisitionOption) -> str:
    if selected is None:
        return "부담을 비교할 정보가 부족해 선택하지 않은 대안"
    differences = []
    if selected.acquisition_mode != alternative.acquisition_mode:
        differences.append(_MODE_LABELS[alternative.acquisition_mode])
    if selected.expected_delay_hours != alternative.expected_delay_hours:
        differences.append("예상 대기 시간이 다름")
    if selected.visit_required != alternative.visit_required:
        differences.append("추가 방문 여부가 다름")
    if selected.direct_cost_band != alternative.direct_cost_band:
        differences.append("비용 범주가 다름")
    return ", ".join(differences) or "공개된 부담 항목이 같음"


def build_guidance_output(
    *,
    case: InteractiveCase,
    view: InteractivePolicyView,
    snapshot: InteractiveSnapshot,
    profile: PatientBurdenProfile,
    decision: AcquisitionDecision,
    catalog: Mapping[str, Sequence[AcquisitionOption]],
    revealed_fact_ids: frozenset[str],
    stop_reason: str | None,
) -> GuidanceOutput:
    """Build patient-facing and detailed outputs from the same selected IDs."""

    groups = trial_groups(snapshot)
    recommendation_views = build_recommendation_views(snapshot.decisions)
    selected = decision.selected_option
    impacts = current_fact_impacts(view, snapshot, revealed_fact_ids)
    if selected is None:
        affected_trial_ids: list[str] = []
        related_criterion_ids: list[str] = []
        fact_description = "현재 자동으로 정할 수 있는 다음 확인 정보가 없음"
    else:
        impact = impacts[selected.fact_id]
        affected_trial_ids = list(impact[2])
        related_criterion_ids = list(impact[3])
        fact_description = next(
            item.description
            for item in view.available_information
            if item.fact_id == selected.fact_id
        )
    preview = _outcome_preview(affected_trial_ids) if affected_trial_ids else None
    current_result = [
        f"현재 자료로 확인된 후보 {len(groups.confirmed_trial_ids)}개",
        f"추가 확인이 필요한 후보 {len(groups.pending_trial_ids)}개",
        f"현재 제외되는 시험 {len(groups.removed_trial_ids)}개",
    ]
    alternatives = list(decision.alternative_options)
    removal_reasons = {
        item.option_id: item.reason for item in decision.decision_trace.removed_options
    }
    all_options = {
        item.option_id: item for options in catalog.values() for item in options
    }
    detailed_alternatives = [
        DetailedAlternative(
            option_id=item.option_id,
            acquisition_mode=item.acquisition_mode,
            difference_from_selected=_difference(selected, item),
            not_selected_reason=removal_reasons.get(
                item.option_id, "정해진 비교 순서에서 선택 경로보다 뒤에 남음"
            ),
        )
        for item in sorted(
            {
                candidate.option_id: candidate
                for candidate in [
                    *alternatives,
                    *(all_options[item] for item in removal_reasons),
                ]
            }.values(),
            key=lambda item: item.option_id,
        )
        if selected is None or item.option_id != selected.option_id
    ]
    patient_choices = [
        _MODE_LABELS[item.acquisition_mode] for item in alternatives[:3]
    ]
    if selected is not None and (
        selected.requires_patient_choice or selected.requires_clinician_authorization
    ):
        patient_choices.insert(0, "환자와 의료진의 확인 뒤 진행 여부 결정")
    if not patient_choices:
        patient_choices.append("다른 낮은 부담 경로 없음")

    patient_message = PatientGuidance(
        fact_id=selected.fact_id if selected else None,
        affected_trial_ids=affected_trial_ids,
        recommendation_views=recommendation_views,
        current_result=current_result,
        next_information=fact_description,
        request_message=None,
        recommended_route=(
            _MODE_LABELS[selected.acquisition_mode]
            if selected
            else "현재 자동으로 권할 경로 없음"
        ),
        existing_or_new=_existing_or_new(selected),
        reason=(
            f"이 정보는 현재 확인 대기 중인 시험 {len(affected_trial_ids)}개에 영향을 준다."
            if selected
            else decision.selection_reason
        ),
        expected_burden=_burden_lines(selected),
        applied_patient_settings=_profile_lines(profile),
        choices_and_alternatives=patient_choices,
        outcome_preview=preview,
        medical_disclaimer=_MEDICAL_DISCLAIMER,
    )
    detailed_selected = None
    if selected is not None:
        detailed_selected = DetailedSelectedOption(
            fact_id=selected.fact_id,
            option_id=selected.option_id,
            action=selected.action,
            acquisition_mode=selected.acquisition_mode,
            affected_trial_ids=affected_trial_ids,
            related_criterion_ids=related_criterion_ids,
            existing_or_new=_existing_or_new(selected),
            expected_delay_hours=selected.expected_delay_hours,
            burden_fields={
                "visit_required": selected.visit_required,
                "direct_cost_band": selected.direct_cost_band.value,
                "physical_burden_0_to_3": selected.physical_burden_0_to_3,
                "emotional_burden_0_to_3": selected.emotional_burden_0_to_3,
                "medical_risk_0_to_3": selected.medical_risk_0_to_3,
                "treatment_disruption_0_to_3": selected.treatment_disruption_0_to_3,
                "new_test_required": selected.new_test_required,
            },
            requires_patient_choice=selected.requires_patient_choice,
            requires_clinician_authorization=selected.requires_clinician_authorization,
            action_status=decision.action_status,
            selection_reason=decision.selection_reason,
        )
    evidence_refs = sorted(
        {
            evidence_id
            for trial in snapshot.decisions
            for assessment in trial.criterion_assessments
            for evidence_id in assessment.evidence_ids
        }
    )
    evidence_refs.extend(
        sorted(
            {
                criterion.source_location
                for trial in view.trials
                for criterion in trial.criteria
                if criterion.criterion_id in related_criterion_ids
            }
        )
    )
    return GuidanceOutput(
        case_id=case.case_id,
        generated_at=snapshot.patient_state.as_of.isoformat(),
        burden_policy_version="patient-burden-v1",
        patient_input_status=profile.input_status,
        preference_mode=profile.preference_mode,
        defaulted_fields=profile.defaulted_fields,
        trial_groups=groups,
        recommendation_views=recommendation_views,
        selected_option=detailed_selected,
        alternatives=detailed_alternatives,
        outcome_preview=preview,
        evidence_refs=evidence_refs,
        stop_reason=stop_reason,
        decision_trace=decision.decision_trace,
        patient_message=patient_message,
        medical_disclaimer=_MEDICAL_DISCLAIMER,
    )


def _status_recovery(
    final: InteractiveSnapshot, full: InteractiveSnapshot
) -> tuple[float, float, float]:
    final_by_id = {item.trial_id: item for item in final.decisions}
    full_by_id = {item.trial_id: item for item in full.decisions}
    ids = sorted(full_by_id)
    trial = sum(
        (
            final_by_id[item].candidate_status,
            final_by_id[item].confirmation_status,
        )
        == (
            full_by_id[item].candidate_status,
            full_by_id[item].confirmation_status,
        )
        for item in ids
    ) / len(ids)
    candidate = sum(
        final_by_id[item].candidate_status is full_by_id[item].candidate_status
        for item in ids
    ) / len(ids)
    confirmation = sum(
        final_by_id[item].confirmation_status
        is full_by_id[item].confirmation_status
        for item in ids
    ) / len(ids)
    return trial, candidate, confirmation


def run_burden_policy(
    *,
    case: InteractiveCase,
    base_profile_id: str,
    split: str,
    mask_id: str,
    patient_profile: PatientBurdenProfile,
    availability: AvailabilityStructure,
    policy_id: AcquisitionPolicyId,
) -> BurdenPolicyRun:
    """Replay one policy; approvals are explicit synthetic benchmark events."""

    view = case.public_policy_view()
    catalog = build_acquisition_catalog(view, availability)
    state = case.initial_patient_state()
    snapshot = evaluate_interactive_case(case, state)
    full = evaluate_interactive_case(case, case.full_patient_state)
    answers = {item.request.fact_id: item.answer.evidence for item in case.hidden_facts}
    feasible_answer_ids = {
        fact_id
        for fact_id, options in catalog.items()
        if any(
            option.available_now
            and not explicit_limit_violations(option, patient_profile)
            for option in options
        )
    }
    feasible_state = case.initial_patient_state().model_copy(
        update={
            "facts": [
                *case.initial_patient_state().facts,
                *(answers[item] for item in sorted(feasible_answer_ids)),
            ]
        }
    )
    burden_feasible = evaluate_interactive_case(case, feasible_state)
    revealed: set[str] = set()
    history: list[BurdenActionRecord] = []
    selected_options: list[AcquisitionOption] = []
    budget = len(case.hidden_facts) if policy_id is AcquisitionPolicyId.ALL_INFORMATION else case.action_budget
    for step in range(1, budget + 1):
        decision = select_acquisition_option(
            view=view,
            snapshot=snapshot,
            revealed_fact_ids=frozenset(revealed),
            catalog=catalog,
            profile=patient_profile,
            policy_id=policy_id,
            selected_options=selected_options,
        )
        selected = decision.selected_option
        if selected is None:
            history.append(
                BurdenActionRecord(
                    step=step,
                    decision=decision,
                    synthetic_authorization_granted=False,
                    answer_released=False,
                )
            )
            break
        requires_authorization = decision.action_status in {
            ActionStatus.AWAITING_PATIENT_CHOICE,
            ActionStatus.AWAITING_CLINICIAN_AUTHORIZATION,
        }
        synthetic_authorization = requires_authorization
        evidence = answers[selected.fact_id]
        state = state.model_copy(update={"facts": [*state.facts, evidence]})
        revealed.add(selected.fact_id)
        selected_options.append(selected)
        history.append(
            BurdenActionRecord(
                step=step,
                decision=decision,
                synthetic_authorization_granted=synthetic_authorization,
                answer_released=True,
            )
        )
        snapshot = evaluate_interactive_case(case, state)

    next_decision = select_acquisition_option(
        view=view,
        snapshot=snapshot,
        revealed_fact_ids=frozenset(revealed),
        catalog=catalog,
        profile=patient_profile,
        policy_id=policy_id,
        selected_options=selected_options,
    )
    stop_reason = (
        "모든 현재 후보 판단이 해결됨"
        if not trial_groups(snapshot).pending_trial_ids
        else (
            "이번 비교의 확인 횟수 한도에 도달"
            if len(selected_options) >= budget
            else next_decision.selection_reason
        )
    )
    guidance = build_guidance_output(
        case=case,
        view=view,
        snapshot=snapshot,
        profile=patient_profile,
        decision=next_decision,
        catalog=catalog,
        revealed_fact_ids=frozenset(revealed),
        stop_reason=stop_reason,
    )
    trial_recovery, candidate_recovery, confirmation_recovery = _status_recovery(
        snapshot, full
    )
    burden_feasible_recovery, _, _ = _status_recovery(snapshot, burden_feasible)
    burden_values = [
        (
            item.physical_burden_0_to_3,
            item.emotional_burden_0_to_3,
            item.medical_risk_0_to_3,
            item.treatment_disruption_0_to_3,
        )
        for item in selected_options
    ]
    unknown_burden = sum(
        value is None for row in burden_values for value in row
    )
    metrics = BurdenRunMetrics(
        trial_status_recovery=trial_recovery,
        burden_feasible_trial_status_recovery=burden_feasible_recovery,
        candidate_status_recovery=candidate_recovery,
        confirmation_status_recovery=confirmation_recovery,
        action_count=len(selected_options),
        new_test_count=sum(item.new_test_required for item in selected_options),
        additional_visit_count=sum(item.visit_required is True for item in selected_options),
        cumulative_delay_hours=sum(
            item.expected_delay_hours or 0 for item in selected_options
        ),
        cumulative_cost_rank=sum(option_cost_rank(item) for item in selected_options),
        cumulative_physical_burden=sum((item[0] or 0) for item in burden_values),
        cumulative_emotional_burden=sum((item[1] or 0) for item in burden_values),
        cumulative_medical_risk=sum((item[2] or 0) for item in burden_values),
        cumulative_treatment_disruption=sum((item[3] or 0) for item in burden_values),
        unknown_cost_count=sum(
            item.direct_cost_band is DirectCostBand.UNKNOWN for item in selected_options
        ),
        unknown_delay_count=sum(
            item.expected_delay_hours is None for item in selected_options
        ),
        unknown_burden_field_count=unknown_burden,
        explicit_limit_violations=sum(
            bool(
                explicit_limit_violations(
                    item, patient_profile, selected_options[:position]
                )
            )
            for position, item in enumerate(selected_options)
        ),
        unauthorized_auto_actions=sum(
            record.answer_released
            and (
                record.decision.action_status
                in {
                    ActionStatus.AWAITING_PATIENT_CHOICE,
                    ActionStatus.AWAITING_CLINICIAN_AUTHORIZATION,
                }
            )
            and not record.synthetic_authorization_granted
            for record in history
        ),
        dominated_option_selections=sum(
            option_is_dominated(item, catalog) for item in selected_options
        ),
        authorization_required_actions=sum(
            record.decision.action_status
            in {
                ActionStatus.AWAITING_PATIENT_CHOICE,
                ActionStatus.AWAITING_CLINICIAN_AUTHORIZATION,
            }
            for record in history
        ),
    )
    return BurdenPolicyRun(
        case_id=case.case_id,
        base_profile_id=base_profile_id,
        split=split,
        disease_group=case.disease_group,
        mask_id=mask_id,
        patient_profile_id=patient_profile.profile_id,
        availability_structure=availability,
        policy_id=policy_id,
        selected_option_ids=[item.option_id for item in selected_options],
        selected_fact_ids=[item.fact_id for item in selected_options],
        action_history=history,
        final_trial_groups=trial_groups(snapshot),
        guidance=guidance,
        metrics=metrics,
    )


def _aggregate(runs: Iterable[BurdenPolicyRun], keys: Sequence[str]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, ...], list[BurdenPolicyRun]] = defaultdict(list)
    for run in runs:
        key = tuple(
            getattr(run, name).value if hasattr(getattr(run, name), "value") else str(getattr(run, name))
            for name in keys
        )
        grouped[key].append(run)
    metric_names = tuple(BurdenRunMetrics.model_fields)
    result = []
    for key, items in sorted(grouped.items()):
        row = dict(zip(keys, key, strict=True))
        row["run_count"] = len(items)
        for metric in metric_names:
            values = [getattr(item.metrics, metric) for item in items]
            row[("mean_" if metric.endswith("recovery") or metric.startswith("cumulative") else "total_") + metric] = (
                mean(values)
                if metric.endswith("recovery") or metric.startswith("cumulative")
                else sum(values)
            )
        result.append(row)
    return result


def _mean_metric(
    runs: Sequence[BurdenPolicyRun], metric: str
) -> float:
    return mean(getattr(item.metrics, metric) for item in runs)


def _adoption_gate(runs: Sequence[BurdenPolicyRun]) -> dict[str, Any]:
    def selected(
        *,
        policy: AcquisitionPolicyId,
        split: str,
        patient_profile_id: str | None = None,
    ) -> list[BurdenPolicyRun]:
        return [
            item
            for item in runs
            if item.policy_id is policy
            and item.split == split
            and (
                patient_profile_id is None
                or item.patient_profile_id == patient_profile_id
            )
        ]

    def comparison(split: str) -> dict[str, float]:
        candidate = selected(policy=AcquisitionPolicyId.PATIENT_ADAPTIVE, split=split)
        baseline = selected(policy=AcquisitionPolicyId.FIXED_ROUTE_COST, split=split)
        constrained_candidate = selected(
            policy=AcquisitionPolicyId.PATIENT_ADAPTIVE,
            split=split,
            patient_profile_id="mobility_cost_constrained",
        )
        constrained_baseline = selected(
            policy=AcquisitionPolicyId.FIXED_ROUTE_COST,
            split=split,
            patient_profile_id="mobility_cost_constrained",
        )
        urgent_candidate = selected(
            policy=AcquisitionPolicyId.PATIENT_ADAPTIVE,
            split=split,
            patient_profile_id="time_urgent",
        )
        urgent_baseline = selected(
            policy=AcquisitionPolicyId.FIXED_ROUTE_COST,
            split=split,
            patient_profile_id="time_urgent",
        )
        new_test_permitted_candidate = [
            item
            for item in candidate
            if item.patient_profile_id in {"low_extra_burden", "time_urgent"}
        ]
        new_test_permitted_baseline = [
            item
            for item in baseline
            if item.patient_profile_id in {"low_extra_burden", "time_urgent"}
        ]
        candidate_burden = sum(
            item.metrics.new_test_count + item.metrics.additional_visit_count
            for item in constrained_candidate
        )
        baseline_burden = sum(
            item.metrics.new_test_count + item.metrics.additional_visit_count
            for item in constrained_baseline
        )
        reduction = (
            1 - candidate_burden / baseline_burden
            if baseline_burden > 0
            else 0.0
        )
        return {
            "candidate_recovery": _mean_metric(candidate, "trial_status_recovery"),
            "baseline_recovery": _mean_metric(baseline, "trial_status_recovery"),
            "recovery_difference": (
                _mean_metric(candidate, "trial_status_recovery")
                - _mean_metric(baseline, "trial_status_recovery")
            ),
            "candidate_burden_feasible_recovery": _mean_metric(
                candidate, "burden_feasible_trial_status_recovery"
            ),
            "baseline_burden_feasible_recovery": _mean_metric(
                baseline, "burden_feasible_trial_status_recovery"
            ),
            "burden_feasible_recovery_difference": (
                _mean_metric(candidate, "burden_feasible_trial_status_recovery")
                - _mean_metric(baseline, "burden_feasible_trial_status_recovery")
            ),
            "new_test_permitted_candidate_recovery": _mean_metric(
                new_test_permitted_candidate, "trial_status_recovery"
            ),
            "new_test_permitted_baseline_recovery": _mean_metric(
                new_test_permitted_baseline, "trial_status_recovery"
            ),
            "new_test_permitted_recovery_difference": (
                _mean_metric(
                    new_test_permitted_candidate, "trial_status_recovery"
                )
                - _mean_metric(
                    new_test_permitted_baseline, "trial_status_recovery"
                )
            ),
            "constrained_candidate_feasible_recovery": _mean_metric(
                constrained_candidate, "burden_feasible_trial_status_recovery"
            ),
            "constrained_baseline_feasible_recovery": _mean_metric(
                constrained_baseline, "burden_feasible_trial_status_recovery"
            ),
            "constrained_feasible_recovery_difference": (
                _mean_metric(
                    constrained_candidate, "burden_feasible_trial_status_recovery"
                )
                - _mean_metric(
                    constrained_baseline, "burden_feasible_trial_status_recovery"
                )
            ),
            "constrained_new_test_visit_candidate": candidate_burden,
            "constrained_new_test_visit_baseline": baseline_burden,
            "constrained_burden_reduction": reduction,
            "urgent_mean_delay_candidate": _mean_metric(
                urgent_candidate, "cumulative_delay_hours"
            ),
            "urgent_mean_delay_baseline": _mean_metric(
                urgent_baseline, "cumulative_delay_hours"
            ),
        }

    development = comparison("development")
    heldout = comparison("heldout")
    adaptive = [item for item in runs if item.policy_id is AcquisitionPolicyId.PATIENT_ADAPTIVE]
    safety_gate = sum(item.metrics.unauthorized_auto_actions for item in adaptive) == 0
    limit_gate = sum(item.metrics.explicit_limit_violations for item in adaptive) == 0
    new_test_permitted_recovery_gate = (
        heldout["new_test_permitted_recovery_difference"] >= -0.02
    )
    constrained_recovery_gate = (
        heldout["constrained_feasible_recovery_difference"] >= -0.02
    )
    burden_gate = heldout["constrained_burden_reduction"] >= 0.20
    delay_gate = (
        heldout["urgent_mean_delay_candidate"]
        <= heldout["urgent_mean_delay_baseline"] + 1e-12
    )
    direction_gate = (
        development["new_test_permitted_recovery_difference"] >= -0.02
        and development["constrained_feasible_recovery_difference"] >= -0.02
        and development["constrained_burden_reduction"] >= 0
        and heldout["constrained_burden_reduction"] >= 0
    )
    gates = {
        "unauthorized_auto_actions_zero": safety_gate,
        "explicit_limit_violations_zero": limit_gate,
        "heldout_new_test_permitted_full_recovery_loss_within_2pp": new_test_permitted_recovery_gate,
        "heldout_constrained_feasible_recovery_loss_within_2pp": constrained_recovery_gate,
        "constrained_new_test_visit_reduction_at_least_20pct": burden_gate,
        "urgent_delay_not_worse": delay_gate,
        "development_and_heldout_direction_consistent": direction_gate,
    }
    return {
        "candidate_policy_id": AcquisitionPolicyId.PATIENT_ADAPTIVE.value,
        "baseline_policy_id": AcquisitionPolicyId.FIXED_ROUTE_COST.value,
        "development": development,
        "heldout": heldout,
        "gates": gates,
        "adoption_gate_passed": all(gates.values()),
    }


def run_public_burden_benchmark(
    config_path: str | Path,
    source_cache: str | Path,
    output_dir: str | Path,
    *,
    action_budget: int = 3,
    progress=None,
) -> Path:
    """Run 360 patient-setting cases and five policy arms without an LLM."""

    if action_budget != 3:
        raise ValueError("the frozen burden benchmark uses action_budget=3")
    spec = load_public_benchmark_spec(config_path)
    source_audit = audit_public_sources(spec, source_cache)
    patient_profiles = benchmark_patient_profiles()
    runs: list[BurdenPolicyRun] = []
    setting_count = 0
    for group in spec.groups:
        for base_profile in group.profiles:
            for mask in group.masks:
                case = build_public_case(
                    group, base_profile, mask, action_budget=action_budget
                )
                for patient_profile in patient_profiles:
                    for availability in _AVAILABILITY_STRUCTURES:
                        for policy_id in _POLICY_IDS:
                            runs.append(
                                run_burden_policy(
                                    case=case,
                                    base_profile_id=base_profile.profile_id,
                                    split=base_profile.split,
                                    mask_id=mask.mask_id,
                                    patient_profile=patient_profile,
                                    availability=availability,
                                    policy_id=policy_id,
                                )
                            )
                        setting_count += 1
                        if progress is not None and setting_count % 60 == 0:
                            progress(f"completed {setting_count}/360 patient settings")

    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    plan = {
        "protocol_id": "clarifytrial-patient-burden-v1",
        "source_protocol_id": spec.protocol_id,
        "base_patient_count": 30,
        "masks_per_patient": 2,
        "patient_profile_count": 3,
        "availability_structure_count": 2,
        "patient_setting_count": 360,
        "policy_count": len(_POLICY_IDS),
        "policy_run_count": len(runs),
        "action_budget": action_budget,
        "all_information_action_budget": 5,
        "model_calls": 0,
        "model_tokens": 0,
        "synthetic_authorization_rule": (
            "새 검사와 사람 판단은 정책이 실행하지 않는다. 비교 환경이 명시적인 "
            "합성 승인 사건을 기록한 뒤에만 숨은 결과를 공개한다."
        ),
        "scope": (
            "공개 시험 조건과 합성 환자 부담 상황의 정책 비교이며 실제 환자 선호, "
            "비용 또는 임상 성능이 아니다."
        ),
        "medical_disclaimer": _MEDICAL_DISCLAIMER,
    }
    _write_json(destination / "plan.json", plan)
    _write_json(destination / "source-audit.json", source_audit)
    _write_json(
        destination / "patient-profiles.json",
        [item.model_dump(mode="json") for item in patient_profiles],
    )
    (destination / "case-results.jsonl").write_text(
        "".join(
            json.dumps(item.model_dump(mode="json"), ensure_ascii=False) + "\n"
            for item in runs
        ),
        encoding="utf-8",
    )
    samples = [
        item.guidance.model_dump(mode="json")
        for item in runs
        if item.policy_id is AcquisitionPolicyId.PATIENT_ADAPTIVE
        and item.split == "heldout"
        and item.mask_id == "A"
        and item.availability_structure
        is AvailabilityStructure.NEW_CONFIRMATION_NEEDED
    ][:6]
    _write_json(destination / "guidance-samples.json", samples)
    summary = {
        **plan,
        "policy_metrics": _aggregate(runs, ("split", "policy_id")),
        "patient_profile_metrics": _aggregate(
            runs, ("split", "patient_profile_id", "policy_id")
        ),
        "availability_metrics": _aggregate(
            runs, ("split", "availability_structure", "policy_id")
        ),
        "adoption_comparison": _adoption_gate(runs),
        "source_audit_criterion_count": len(source_audit),
    }
    summary_path = destination / "summary.json"
    _write_json(summary_path, summary)
    return summary_path
