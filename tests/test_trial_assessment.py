from datetime import datetime, timezone

from clarifytrial.agents import MatcherJudgeAgent
from clarifytrial.contracts import (
    NextAction,
    NextEvidenceRequest,
    PatientState,
    TrialCriterion,
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
