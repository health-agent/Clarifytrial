from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Mapping

import pytest

from clarifytrial.agents import (
    CoordinatorAgent,
    MatcherJudgeAgent,
    NextEvidenceAgent,
    SelectiveReviewerAgent,
)
from clarifytrial.contracts import (
    ComparisonOperator,
    CriterionKind,
    EvidenceRequirement,
    EvidenceFact,
    EvidenceSourceType,
    NextAction,
    NextEvidenceRequest,
    NumericConstraint,
    PatientState,
    TrialCriterion,
    VerificationStatus,
)
from clarifytrial.environment import (
    HiddenFactAnswer,
    HiddenPatientEnvironment,
    PublicFactRequest,
    PublicQuestionCatalog,
    SyntheticInformationTools,
)
from clarifytrial.llm import ScriptedStructuredModel
from clarifytrial.settings import EpisodeSettings
from clarifytrial.trace import TraceRecorder
from clarifytrial.workflow import (
    EpisodeAgents,
    EpisodeCase,
    EpisodeRunner,
    EpisodeStopReason,
    WorkflowProtocolError,
)


def _case(*, with_request: bool = True) -> EpisodeCase:
    requests = []
    if with_request:
        requests = [
            NextEvidenceRequest(
                fact_id="fresh-platelets",
                description="Platelet result collected within the last 14 days",
                related_criterion_ids=["criterion-platelets"],
                acceptable_actions=[NextAction.REQUEST_VERIFICATION],
                reason="The protocol requires a recent official result.",
            )
        ]
    return EpisodeCase(
        case_id="synthetic-stale-lab",
        trial_id="NCT-SYNTHETIC-001",
        criteria=[
            TrialCriterion(
                criterion_id="criterion-platelets",
                trial_id="NCT-SYNTHETIC-001",
                kind=CriterionKind.INCLUSION,
                statement="Platelets at least 100 x10^9/L within 14 days",
                source_location="protocol#inclusion-4",
                numeric_constraint=NumericConstraint(
                    concept="platelet_count",
                    operator=ComparisonOperator.GTE,
                    threshold=100,
                    unit="10^9/L",
                ),
                evidence_requirement=EvidenceRequirement(
                    max_age_days=14,
                    allowed_source_types=[
                        EvidenceSourceType.OFFICIAL_VERIFICATION
                    ],
                    allowed_verification_statuses=[
                        VerificationStatus.VERIFIED
                    ],
                ),
            )
        ],
        initial_patient_state=PatientState(
            patient_id="synthetic-patient-001",
            as_of=datetime(2026, 8, 20, tzinfo=timezone.utc),
            facts=[
                EvidenceFact(
                    evidence_id="old-platelets",
                    statement="Platelets were 132 x10^9/L three months ago.",
                    source_type=EvidenceSourceType.MEDICAL_RECORD,
                    source_location="synthetic-ehr#lab-old",
                    event_date=date(2026, 5, 20),
                    recorded_date=date(2026, 5, 20),
                    verification_status=VerificationStatus.VERIFIED,
                    concept="platelet_count",
                    value=132,
                    unit="10^9/L",
                )
            ],
        ),
        evidence_requests=requests,
    )


def _tools() -> SyntheticInformationTools:
    catalog = PublicQuestionCatalog(
        [
            PublicFactRequest(
                fact_id="fresh-platelets",
                description="Platelet result collected within the last 14 days",
                available_actions=(NextAction.REQUEST_VERIFICATION,),
            )
        ]
    )
    environment = HiddenPatientEnvironment(
        [
            HiddenFactAnswer(
                fact_id="fresh-platelets",
                access_path=NextAction.REQUEST_VERIFICATION,
                evidence=EvidenceFact(
                    evidence_id="fresh-platelets-result",
                    statement="Platelets were 126 x10^9/L on 2026-08-18.",
                    source_type=EvidenceSourceType.OFFICIAL_VERIFICATION,
                    source_location="synthetic-central-lab#result-001",
                    event_date=date(2026, 8, 18),
                    recorded_date=date(2026, 8, 18),
                    verification_status=VerificationStatus.VERIFIED,
                    concept="platelet_count",
                    value=126,
                    unit="10^9/L",
                ),
            )
        ]
    )
    return SyntheticInformationTools(catalog, environment)


def _agents(model: ScriptedStructuredModel) -> EpisodeAgents:
    return EpisodeAgents(
        coordinator=CoordinatorAgent(model),
        matcher_judge=MatcherJudgeAgent(model),
        next_evidence=NextEvidenceAgent(model),
        selective_reviewer=SelectiveReviewerAgent(model),
    )


def test_stale_lab_is_retained_then_confirmed_after_official_result() -> None:
    matcher_inputs: list[Mapping[str, Any]] = []

    def coordinate(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        route = payload["allowed_routes"][0]
        return {
            "route": route,
            "target_ids": payload.get("dirty_criterion_ids", []),
            "reason_code": "permitted_state_transition",
            "reason": "Select the permitted next state.",
        }

    def match(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        matcher_inputs.append(payload)
        evidence_ids = {
            item["evidence_id"] for item in payload["patient_facts"]
        }
        if "fresh-platelets-result" in evidence_ids:
            return {
                "assessments": [
                    {
                        "criterion_id": "criterion-platelets",
                        "criterion_source_location": "protocol#inclusion-4",
                        "clinical_status": "supports",
                        "evidence_sufficiency": "sufficient",
                        "evidence_ids": ["fresh-platelets-result"],
                        "missing_information_ids": [],
                        "rationale": "The recent official result meets the threshold.",
                        "review_flags": [],
                    }
                ]
            }
        return {
            "assessments": [
                {
                    "criterion_id": "criterion-platelets",
                    "criterion_source_location": "protocol#inclusion-4",
                    "clinical_status": "supports",
                    "evidence_sufficiency": "insufficient",
                    "evidence_ids": ["old-platelets"],
                    "missing_information_ids": ["fresh-platelets"],
                    "rationale": "The old result supports possibility but is outside 14 days.",
                    "review_flags": [],
                }
            ]
        }

    model = ScriptedStructuredModel(
        {
            "coordinator": coordinate,
            "matcher_judge": match,
            "next_evidence": lambda _: {
                "action": "REQUEST_VERIFICATION",
                "target_fact_id": "fresh-platelets",
                "related_criterion_ids": ["criterion-platelets"],
                "reason": "A recent official result can finish this criterion.",
                "message": "Request a platelet result from the last 14 days.",
            },
            "selective_reviewer": lambda _: {
                "conclusion_id": "trial:NCT-SYNTHETIC-001",
                "decision": "approve",
                "patient_evidence_ids": [],
                "trial_evidence_ids": [],
                "affected_condition_ids": [],
                "missing_fact_ids": [],
                "reason_code": "not_used",
                "reason": "This handler is not called in the stale-lab case.",
            },
        }
    )
    trace = TraceRecorder("synthetic-stale-lab")
    result = EpisodeRunner(
        _agents(model),
        EpisodeSettings(
            max_external_actions=3,
            max_selective_reviews=1,
            max_cycles=6,
        ),
    ).run(_case(), _tools(), trace=trace)

    assert result.stop_reason is EpisodeStopReason.CONFIRMED
    assert result.final_decision.candidate_status.value == "retain"
    assert result.final_decision.confirmation_status.value == "confirmed"
    assert result.final_decision.next_action.action is NextAction.NONE
    assert [item.action for item in result.action_history] == [
        NextAction.REQUEST_VERIFICATION
    ]
    assert result.action_history[0].new_facts[0].evidence_id == (
        "fresh-platelets-result"
    )
    assert model.call_count == {
        "coordinator": 4,
        "matcher_judge": 2,
        "next_evidence": 1,
    }
    assert "fresh-platelets-result" not in {
        item["evidence_id"] for item in matcher_inputs[0]["patient_facts"]
    }
    assert "fresh-platelets-result" in {
        item["evidence_id"] for item in matcher_inputs[1]["patient_facts"]
    }
    assert any(event.actor == "decision_rules" for event in trace.events)
    assert any(event.actor == "mechanical_checks" for event in trace.events)
    assert any(
        event.actor == "synthetic_information_tools" for event in trace.events
    )


def test_selective_reviewer_is_called_once_for_a_structural_defect() -> None:
    case = _case(with_request=False)

    def coordinate(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "route": payload["allowed_routes"][0],
            "target_ids": payload.get("dirty_criterion_ids", []),
            "reason_code": "permitted_state_transition",
            "reason": "Select the permitted next state.",
        }

    model = ScriptedStructuredModel(
        {
            "coordinator": coordinate,
            "matcher_judge": lambda _: {
                "assessments": [
                    {
                        "criterion_id": "criterion-platelets",
                        "criterion_source_location": "protocol#inclusion-4",
                        "clinical_status": "supports",
                        "evidence_sufficiency": "sufficient",
                        "evidence_ids": ["old-platelets"],
                        "missing_information_ids": [],
                        "rationale": "The supplied fact supports the threshold.",
                        "review_flags": [],
                    }
                ]
            },
            "next_evidence": lambda _: pytest.fail("next evidence was not expected"),
            "selective_reviewer": lambda _: {
                "conclusion_id": "trial:NCT-SYNTHETIC-001",
                "decision": "approve",
                "patient_evidence_ids": ["old-platelets"],
                "trial_evidence_ids": ["criterion-platelets"],
                "affected_condition_ids": [],
                "missing_fact_ids": [],
                "reason_code": "sources_checked",
                "reason": "The supplied sources support the conclusion.",
            },
        }
    )
    result = EpisodeRunner(
        _agents(model),
        EpisodeSettings(
            max_external_actions=3,
            max_selective_reviews=1,
            max_cycles=5,
        ),
    ).run(case, _tools())

    assert result.stop_reason is EpisodeStopReason.CONFIRMED
    assert len(result.review_history) == 1
    review_flags = result.decision_history[0].criterion_assessments[0].review_flags
    assert [flag.value for flag in review_flags] == ["code_model_mismatch"]
    assert not result.final_decision.review_required
    assert model.call_count["selective_reviewer"] == 1


def test_coordinator_cannot_skip_the_required_initial_match() -> None:
    model = ScriptedStructuredModel(
        {
            "coordinator": lambda _: {
                "route": "FINISH",
                "target_ids": [],
                "reason_code": "invalid_skip",
                "reason": "Try to skip the initial assessment.",
            },
            "matcher_judge": lambda _: pytest.fail("matcher was not called"),
            "next_evidence": lambda _: pytest.fail("next evidence was not called"),
            "selective_reviewer": lambda _: pytest.fail("reviewer was not called"),
        }
    )
    runner = EpisodeRunner(
        _agents(model),
        EpisodeSettings(
            max_external_actions=3,
            max_selective_reviews=1,
            max_cycles=3,
        ),
    )

    with pytest.raises(WorkflowProtocolError, match="allowed routes are MATCHER_JUDGE"):
        runner.run(_case(), _tools())
