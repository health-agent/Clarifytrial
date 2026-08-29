from datetime import date, datetime, timezone
from typing import Any, Mapping

from clarifytrial.agents import MatcherJudgeAgent
from clarifytrial.contracts import (
    ComparisonOperator,
    CriterionKind,
    EvidenceFact,
    EvidenceSourceType,
    NextAction,
    NextEvidenceRequest,
    NumericConstraint,
    PatientState,
    ReviewFlag,
    TrialCriterion,
    VerificationStatus,
)
from clarifytrial.llm import ScriptedStructuredModel
from clarifytrial.trace import TraceRecorder
from clarifytrial.workflow.trial_assessment import assess_criteria_bundle


def test_unstructured_judgment_cannot_add_a_new_missing_fact_identifier() -> None:
    criterion = TrialCriterion(
        criterion_id="T1:C1",
        trial_id="T1",
        kind="inclusion",
        statement="현재 치료 계획을 확인해야 한다.",
        source_location="synthetic:T1#C1",
    )
    request = NextEvidenceRequest(
        fact_id="declared-treatment-plan",
        description="현재 치료 계획",
        related_criterion_ids=[criterion.criterion_id],
        acceptable_actions=[NextAction.LOOKUP_RECORD],
        reason="현재 자료에 치료 계획이 없다.",
    )
    model = ScriptedStructuredModel(
        {
            "matcher_judge": lambda _: {
                "assessments": [
                    {
                        "criterion_id": criterion.criterion_id,
                        "criterion_source_location": criterion.source_location,
                        "clinical_status": "unknown",
                        "evidence_sufficiency": "insufficient",
                        "evidence_ids": [],
                        "missing_information_ids": ["invented-treatment-plan"],
                        "rationale": "현재 치료 계획 자료가 없다.",
                        "review_flags": [],
                    }
                ]
            }
        }
    )
    trace = TraceRecorder("missing-id-correction")

    result = assess_criteria_bundle(
        case_id="P1",
        criteria=[criterion],
        patient_state=PatientState(
            patient_id="P1",
            as_of=datetime(2026, 8, 29, tzinfo=timezone.utc),
            facts=[],
        ),
        evidence_requests=[request],
        matcher_judge=MatcherJudgeAgent(model),
        trace=trace,
        cycle=1,
    )

    assert result[0].missing_information_ids == ["declared-treatment-plan"]
    assert any(
        item.event == "missing_information_identifiers_corrected"
        for item in trace.events
    )


def test_mixed_bundle_sends_only_free_text_criteria_and_keeps_input_order() -> None:
    free_text_with_evidence = TrialCriterion(
        criterion_id="T1:C-note",
        trial_id="T1",
        kind=CriterionKind.EXCLUSION,
        statement="현재 흉관을 사용 중이면 제외한다.",
        source_location="synthetic:T1#C-note",
    )
    structured = TrialCriterion(
        criterion_id="T1:C-platelets",
        trial_id="T1",
        kind=CriterionKind.INCLUSION,
        statement="혈소판 수치가 100 이상이어야 한다.",
        source_location="synthetic:T1#C-platelets",
        numeric_constraint=NumericConstraint(
            concept="platelet_count",
            operator=ComparisonOperator.GTE,
            threshold=100,
            unit="10^9/L",
        ),
    )
    free_text_missing = TrialCriterion(
        criterion_id="T1:C-plan",
        trial_id="T1",
        kind=CriterionKind.INCLUSION,
        statement="현재 치료 계획을 확인해야 한다.",
        source_location="synthetic:T1#C-plan",
    )
    state = PatientState(
        patient_id="P1",
        as_of=datetime(2026, 8, 29, tzinfo=timezone.utc),
        facts=[
            EvidenceFact(
                evidence_id="note-evidence",
                statement="현재 흉관을 사용하지 않는다.",
                source_type=EvidenceSourceType.SYNTHETIC_CASE,
                source_location="synthetic:P1#note",
                verification_status=VerificationStatus.VERIFIED,
            ),
            EvidenceFact(
                evidence_id="platelet-evidence",
                statement="혈소판 수치는 120 x10^9/L이다.",
                source_type=EvidenceSourceType.SYNTHETIC_CASE,
                source_location="synthetic:P1#platelets",
                event_date=date(2026, 8, 28),
                verification_status=VerificationStatus.VERIFIED,
                concept="platelet_count",
                value=120,
                unit="10^9/L",
            ),
        ],
    )
    treatment_request = NextEvidenceRequest(
        fact_id="treatment-plan",
        description="현재 치료 계획",
        related_criterion_ids=[free_text_missing.criterion_id],
        acceptable_actions=[NextAction.LOOKUP_RECORD],
        reason="현재 자료에 치료 계획이 없다.",
    )
    matcher_inputs: list[Mapping[str, Any]] = []

    def match(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        matcher_inputs.append(payload)
        requested_ids = [item["criterion_id"] for item in payload["criteria"]]
        returned_id = requested_ids[-1]
        if returned_id == free_text_missing.criterion_id:
            return {
                "assessments": [
                    {
                        "criterion_id": returned_id,
                        "criterion_source_location": free_text_missing.source_location,
                        "clinical_status": "unknown",
                        "evidence_sufficiency": "insufficient",
                        "evidence_ids": [],
                        "missing_information_ids": ["treatment-plan"],
                        "rationale": "현재 치료 계획 자료가 없다.",
                        "review_flags": [],
                    }
                ]
            }
        return {
            "assessments": [
                {
                    "criterion_id": returned_id,
                    "criterion_source_location": (
                        free_text_with_evidence.source_location
                    ),
                    "clinical_status": "supports",
                    "evidence_sufficiency": "sufficient",
                    "evidence_ids": ["note-evidence"],
                    "missing_information_ids": [],
                    "rationale": "현재 흉관을 사용하지 않는다는 기록이 있다.",
                    "review_flags": [],
                }
            ]
        }

    model = ScriptedStructuredModel({"matcher_judge": match})
    trace = TraceRecorder("mixed-bundle")
    result = assess_criteria_bundle(
        case_id="P1",
        criteria=[free_text_with_evidence, structured, free_text_missing],
        patient_state=state,
        evidence_requests=[treatment_request],
        matcher_judge=MatcherJudgeAgent(model),
        trace=trace,
        cycle=2,
    )

    assert [item.criterion_id for item in result] == [
        free_text_with_evidence.criterion_id,
        structured.criterion_id,
        free_text_missing.criterion_id,
    ]
    assert result[1].evidence_ids == ["platelet-evidence"]
    assert result[1].missing_information_ids == []
    assert [
        [item["criterion_id"] for item in payload["criteria"]]
        for payload in matcher_inputs
    ] == [
        [free_text_with_evidence.criterion_id, free_text_missing.criterion_id],
        [free_text_with_evidence.criterion_id],
    ]
    assert all(
        structured.criterion_id not in payload["mechanical_checks"]
        for payload in matcher_inputs
    )
    assert any(item.event == "missing_criteria_retried" for item in trace.events)
    assert not any(
        item.event == "model_assessments_replaced" for item in trace.events
    )


def test_all_structured_bundle_never_calls_model_and_preserves_flags_and_ids() -> None:
    missing = TrialCriterion(
        criterion_id="T1:C-missing",
        trial_id="T1",
        kind=CriterionKind.INCLUSION,
        statement="혈소판 수치가 100 이상이어야 한다.",
        source_location="synthetic:T1#C-missing",
        numeric_constraint=NumericConstraint(
            concept="platelet_count",
            operator=ComparisonOperator.GTE,
            threshold=100,
            unit="10^9/L",
        ),
    )
    conflicting = TrialCriterion(
        criterion_id="T1:C-conflict",
        trial_id="T1",
        kind=CriterionKind.INCLUSION,
        statement="HbA1c가 7% 미만이어야 한다.",
        source_location="synthetic:T1#C-conflict",
        numeric_constraint=NumericConstraint(
            concept="hba1c",
            operator=ComparisonOperator.LT,
            threshold=7,
            unit="%",
        ),
    )
    state = PatientState(
        patient_id="P1",
        as_of=datetime(2026, 8, 29, tzinfo=timezone.utc),
        facts=[
            EvidenceFact(
                evidence_id="hba1c-high",
                statement="HbA1c는 8%이다.",
                source_type=EvidenceSourceType.SYNTHETIC_CASE,
                source_location="synthetic:P1#hba1c-high",
                event_date=date(2026, 8, 28),
                verification_status=VerificationStatus.VERIFIED,
                concept="hba1c",
                value=8,
                unit="%",
            ),
            EvidenceFact(
                evidence_id="hba1c-low",
                statement="같은 날 HbA1c는 6%로 기록됐다.",
                source_type=EvidenceSourceType.SYNTHETIC_CASE,
                source_location="synthetic:P1#hba1c-low",
                event_date=date(2026, 8, 28),
                verification_status=VerificationStatus.VERIFIED,
                concept="hba1c",
                value=6,
                unit="%",
            ),
        ],
    )
    request = NextEvidenceRequest(
        fact_id="platelet-result",
        description="혈소판 검사 결과",
        related_criterion_ids=[missing.criterion_id],
        acceptable_actions=[NextAction.LOOKUP_RECORD],
        reason="현재 자료에 혈소판 검사 결과가 없다.",
    )
    model = ScriptedStructuredModel({})
    trace = TraceRecorder("all-structured")
    result = assess_criteria_bundle(
        case_id="P1",
        criteria=[missing, conflicting],
        patient_state=state,
        evidence_requests=[request],
        matcher_judge=MatcherJudgeAgent(model),
        trace=trace,
        cycle=3,
    )

    assert [item.criterion_id for item in result] == [
        missing.criterion_id,
        conflicting.criterion_id,
    ]
    assert result[0].missing_information_ids == ["platelet-result"]
    assert result[0].evidence_ids == []
    assert result[1].evidence_ids == ["hba1c-high", "hba1c-low"]
    assert result[1].review_flags == [ReviewFlag.EVIDENCE_CONFLICT]
    assert result[1].missing_information_ids == []
    assert model.call_count.get("matcher_judge", 0) == 0
    applied = [
        item
        for item in trace.events
        if item.event == "structured_criteria_applied_without_model"
    ]
    assert len(applied) == 1
    assert applied[0].input_refs == [missing.criterion_id, conflicting.criterion_id]
    assert applied[0].output == {
        "criterion_count": 2,
        "criterion_ids": [missing.criterion_id, conflicting.criterion_id],
    }
    assert not any(
        item.actor == "matcher_judge" or item.event == "model_assessments_replaced"
        for item in trace.events
    )
