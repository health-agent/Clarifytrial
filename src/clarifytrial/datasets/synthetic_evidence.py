"""Shared synthetic evidence routes used by executable evaluation datasets."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Literal

from ..contracts import (
    EvidenceFact,
    EvidenceSourceType,
    NextAction,
    VerificationStatus,
)


AcquisitionModeValue = Literal[
    "internal_record",
    "outside_record",
    "patient_report",
    "existing_official_result",
    "new_noninvasive_test",
]


def source_policy(
    mode: AcquisitionModeValue,
) -> tuple[EvidenceSourceType, VerificationStatus, NextAction]:
    if mode == "patient_report":
        return (
            EvidenceSourceType.PATIENT_REPORT,
            VerificationStatus.REPORTED,
            NextAction.ASK_PATIENT,
        )
    if mode in {"internal_record", "outside_record"}:
        return (
            EvidenceSourceType.MEDICAL_RECORD,
            VerificationStatus.VERIFIED,
            NextAction.LOOKUP_RECORD,
        )
    return (
        EvidenceSourceType.OFFICIAL_VERIFICATION,
        VerificationStatus.VERIFIED,
        NextAction.REQUEST_VERIFICATION,
    )


def acquisition_option(
    *,
    fact_id: str,
    mode: AcquisitionModeValue,
) -> dict[str, object]:
    """Return one explicit information path and its separate burden fields."""

    _, _, action = source_policy(mode)
    if mode == "patient_report":
        values = {
            "available_now": True,
            "expected_delay_hours": 0,
            "visit_required": False,
            "direct_cost_band": "none",
            "physical_burden_0_to_3": 0,
            "emotional_burden_0_to_3": 1,
            "medical_risk_0_to_3": 0,
            "treatment_disruption_0_to_3": 0,
            "source_note": "합성 환자가 직접 답할 수 있는 정보",
        }
    elif mode in {"internal_record", "outside_record"}:
        values = {
            "available_now": True,
            "expected_delay_hours": 2 if mode == "internal_record" else 24,
            "visit_required": False,
            "direct_cost_band": "none",
            "physical_burden_0_to_3": 0,
            "emotional_burden_0_to_3": 0,
            "medical_risk_0_to_3": 0,
            "treatment_disruption_0_to_3": 0,
            "source_note": "합성 평가에서 기존 기록으로 확인할 정보",
        }
    elif mode == "existing_official_result":
        values = {
            "available_now": True,
            "expected_delay_hours": 4,
            "visit_required": False,
            "direct_cost_band": "none",
            "physical_burden_0_to_3": 0,
            "emotional_burden_0_to_3": 0,
            "medical_risk_0_to_3": 0,
            "treatment_disruption_0_to_3": 0,
            "source_note": "합성 평가에서 이미 받은 공식 결과를 확인",
        }
    elif mode == "new_noninvasive_test":
        values = {
            "available_now": True,
            "expected_delay_hours": 48,
            "visit_required": True,
            "direct_cost_band": "medium",
            "physical_burden_0_to_3": 1,
            "emotional_burden_0_to_3": 1,
            "medical_risk_0_to_3": 1,
            "treatment_disruption_0_to_3": 0,
            "new_test_required": True,
            "requires_patient_choice": True,
            "requires_clinician_authorization": True,
            "source_note": "합성 평가에서 새 비침습 검사가 필요한 정보",
        }
    else:
        raise ValueError(f"unsupported synthetic acquisition mode: {mode}")
    return {
        "option_id": f"{fact_id}:{mode}",
        "fact_id": fact_id,
        "action": action.value,
        "acquisition_mode": mode,
        **values,
    }


def synthetic_fact(
    *,
    patient_id: str,
    group_id: str,
    fact_code: str,
    description: str,
    value: float,
    unit: str,
    mode: AcquisitionModeValue,
    as_of: datetime,
    source_namespace: str,
) -> EvidenceFact:
    source_type, verification, _ = source_policy(mode)
    return EvidenceFact(
        evidence_id=f"{patient_id}:{fact_code}:answer",
        statement=f"합성 환자 {description}: {value:g} {unit}",
        source_type=source_type,
        source_location=f"{source_namespace}:{patient_id}#{fact_code}",
        event_date=as_of.date() - timedelta(days=2),
        recorded_date=as_of.date() - timedelta(days=1),
        verification_status=verification,
        concept=f"{group_id}:{fact_code}",
        value=value,
        unit=unit,
    )


__all__ = [
    "AcquisitionModeValue",
    "acquisition_option",
    "source_policy",
    "synthetic_fact",
]
