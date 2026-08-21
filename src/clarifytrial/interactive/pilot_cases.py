"""Mechanically authored synthetic cases for the first interactive pilot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from ..contracts import (
    ComparisonOperator,
    CriterionKind,
    EvidenceFact,
    EvidenceRequirement,
    EvidenceSourceType,
    NextAction,
    NumericConstraint,
    PatientState,
    TrialCriterion,
    VerificationStatus,
)
from ..environment import HiddenFactAnswer, PublicFactRequest
from .contracts import InteractiveCase, InteractiveHiddenFact, InteractiveTrial


@dataclass(frozen=True, slots=True)
class _FactTemplate:
    code: str
    description: str
    concept: str
    unit: str
    value: float
    operator: ComparisonOperator
    threshold: float
    route: NextAction


_DISEASES: dict[str, tuple[str, tuple[_FactTemplate, ...]]] = {
    "type_2_diabetes": (
        "2형 당뇨병",
        (
            _FactTemplate("hba1c", "최근 HbA1c 검사 결과", "hba1c", "%", 6.4, ComparisonOperator.GTE, 7.0, NextAction.REQUEST_VERIFICATION),
            _FactTemplate("egfr", "최근 eGFR 검사 결과", "egfr", "mL/min/1.73m2", 34, ComparisonOperator.GTE, 45, NextAction.LOOKUP_RECORD),
            _FactTemplate("injection", "주사 치료를 받을 의사", "accepts_injection", "bool", 1, ComparisonOperator.EQ, 1, NextAction.ASK_PATIENT),
            _FactTemplate("bmi", "최근 측정 BMI", "bmi", "kg/m2", 29, ComparisonOperator.GTE, 25, NextAction.REQUEST_VERIFICATION),
            _FactTemplate("stable_med", "현재 약물 용량을 유지한 기간", "stable_medication_days", "days", 56, ComparisonOperator.GTE, 42, NextAction.LOOKUP_RECORD),
        ),
    ),
    "breast_cancer": (
        "유방암",
        (
            _FactTemplate("er", "병리검사에서 확인한 ER 양성 여부", "er_positive", "bool", 0, ComparisonOperator.EQ, 1, NextAction.REQUEST_VERIFICATION),
            _FactTemplate("ecog", "최근 진료의 ECOG 활동수준", "ecog_status", "score", 2, ComparisonOperator.LTE, 1, NextAction.LOOKUP_RECORD),
            _FactTemplate("visits", "매주 연구기관에 방문할 수 있는지", "accepts_weekly_visits", "bool", 1, ComparisonOperator.EQ, 1, NextAction.ASK_PATIENT),
            _FactTemplate("anc", "최근 절대호중구수 검사 결과", "absolute_neutrophil_count", "10^9/L", 2.1, ComparisonOperator.GTE, 1.5, NextAction.REQUEST_VERIFICATION),
            _FactTemplate("chemo", "기존 항암치료 차수", "prior_chemotherapy_lines", "count", 1, ComparisonOperator.LTE, 1, NextAction.LOOKUP_RECORD),
        ),
    ),
    "major_depressive_disorder": (
        "주요우울장애",
        (
            _FactTemplate("phq9", "최근 PHQ-9 평가 점수", "phq9_score", "score", 8, ComparisonOperator.GTE, 10, NextAction.REQUEST_VERIFICATION),
            _FactTemplate("stable_dose", "현재 항우울제 용량을 유지한 기간", "stable_antidepressant_days", "days", 21, ComparisonOperator.GTE, 42, NextAction.LOOKUP_RECORD),
            _FactTemplate("visits", "매주 상담에 참여할 수 있는지", "accepts_weekly_sessions", "bool", 1, ComparisonOperator.EQ, 1, NextAction.ASK_PATIENT),
            _FactTemplate("no_plan", "현재 구체적인 자살 계획이 없는지에 대한 공식 평가", "no_active_suicide_plan", "bool", 1, ComparisonOperator.EQ, 1, NextAction.REQUEST_VERIFICATION),
            _FactTemplate("substance", "물질 사용 없이 지낸 기간", "substance_free_days", "days", 45, ComparisonOperator.GTE, 30, NextAction.LOOKUP_RECORD),
        ),
    ),
}


_SOURCE_BY_ROUTE = {
    NextAction.ASK_PATIENT: (
        EvidenceSourceType.PATIENT_REPORT,
        VerificationStatus.REPORTED,
    ),
    NextAction.LOOKUP_RECORD: (
        EvidenceSourceType.MEDICAL_RECORD,
        VerificationStatus.VERIFIED,
    ),
    NextAction.REQUEST_VERIFICATION: (
        EvidenceSourceType.OFFICIAL_VERIFICATION,
        VerificationStatus.VERIFIED,
    ),
}


def _one_case(disease_key: str, case_number: int) -> InteractiveCase:
    disease_label, templates = _DISEASES[disease_key]
    case_id = f"pilot-{disease_key}-{case_number:02d}"
    patient_id = f"synthetic-{disease_key}-{case_number:02d}"
    as_of = datetime(2026, 8, 20, 12, tzinfo=timezone.utc)
    visible = EvidenceFact(
        evidence_id=f"{case_id}-diagnosis",
        statement=f"합성 환자에게 {disease_label} 진단이 기록되어 있다.",
        source_type=EvidenceSourceType.SYNTHETIC_CASE,
        source_location=f"synthetic-case:{case_id}#diagnosis",
        event_date=date(2026, 7, 1),
        recorded_date=date(2026, 7, 1),
        verification_status=VerificationStatus.VERIFIED,
        concept="diagnosis_present",
        value=1,
        unit="bool",
    )
    facts: list[EvidenceFact] = [visible]
    hidden_by_code: dict[str, InteractiveHiddenFact] = {}
    for position, template in enumerate(templates):
        source_type, verification = _SOURCE_BY_ROUTE[template.route]
        # Small deterministic variation prevents the 12 cases from being byte
        # copies while preserving the intended pass/fail direction.
        variation = (case_number - 2.5) * 0.02
        value = template.value
        if template.unit not in {"bool", "count", "days", "score"}:
            value = round(value + variation, 2)
        evidence_id = f"{case_id}-{template.code}-answer"
        fact_id = f"{case_id}-{template.code}"
        event_day = as_of.date() - timedelta(days=position + 1)
        evidence = EvidenceFact(
            evidence_id=evidence_id,
            statement=f"{template.description}: {value:g} {template.unit}",
            source_type=source_type,
            source_location=f"synthetic-{source_type.value}:{case_id}#{template.code}",
            event_date=event_day,
            recorded_date=event_day,
            verification_status=verification,
            concept=template.concept,
            value=value,
            unit=template.unit,
        )
        facts.append(evidence)
        public = PublicFactRequest(
            fact_id=fact_id,
            description=template.description,
            available_actions=(template.route,),
        )
        hidden_by_code[template.code] = InteractiveHiddenFact(
            request=public,
            answer=HiddenFactAnswer(
                fact_id=fact_id,
                access_path=template.route,
                evidence=evidence,
            ),
        )

    template_by_code = {item.code: item for item in templates}
    codes = [item.code for item in templates]
    trial_fact_codes = [
        [codes[0]],
        [codes[1]],
        [codes[2]],
        [codes[0], codes[3]],
        [codes[1], codes[4]],
    ]
    trials: list[InteractiveTrial] = []
    for trial_position, fact_codes in enumerate(trial_fact_codes, start=1):
        trial_id = f"SYNTH-{disease_key.upper()}-{case_number:02d}-{trial_position}"
        criteria: list[TrialCriterion] = []
        for criterion_position, code in enumerate(fact_codes, start=1):
            template = template_by_code[code]
            source_type, verification = _SOURCE_BY_ROUTE[template.route]
            criteria.append(
                TrialCriterion(
                    criterion_id=f"{trial_id}-c{criterion_position}",
                    trial_id=trial_id,
                    kind=CriterionKind.INCLUSION,
                    statement=(
                        f"{template.description}: {template.operator.value} "
                        f"{template.threshold:g} {template.unit}"
                    ),
                    source_location=f"synthetic-protocol:{trial_id}#c{criterion_position}",
                    numeric_constraint=NumericConstraint(
                        concept=template.concept,
                        operator=template.operator,
                        threshold=template.threshold,
                        unit=template.unit,
                    ),
                    evidence_requirement=EvidenceRequirement(
                        max_age_days=120,
                        allowed_source_types=[source_type],
                        allowed_verification_statuses=[verification],
                    ),
                )
            )
        trials.append(InteractiveTrial(trial_id=trial_id, criteria=criteria))

    # Distractor facts are deliberately listed first. A policy that simply
    # asks the public list in order will spend its budget before recovering the
    # full-information decisions. Dynamic impact policies can discover that
    # the distractors no longer matter after the shared trial is excluded.
    authored_order = [codes[3], codes[4], codes[2], codes[1], codes[0]]
    return InteractiveCase(
        case_id=case_id,
        disease_group=disease_label,
        full_patient_state=PatientState(
            patient_id=patient_id,
            as_of=as_of,
            facts=facts,
        ),
        initial_visible_evidence_ids=[visible.evidence_id],
        trials=trials,
        hidden_facts=[hidden_by_code[code] for code in authored_order],
        action_budget=3,
    )


def build_interactive_pilot_cases() -> list[InteractiveCase]:
    """Return 12 deterministic patients: four per declared disease group."""

    return [
        _one_case(disease_key, case_number)
        for disease_key in _DISEASES
        for case_number in range(1, 5)
    ]

