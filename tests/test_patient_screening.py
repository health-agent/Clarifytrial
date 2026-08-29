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
    EvidenceFact,
    EvidenceSourceType,
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
from clarifytrial.interactive import AcquisitionMode, AcquisitionOption
from clarifytrial.interactive.burden_contracts import DirectCostBand
from clarifytrial.llm import ScriptedStructuredModel
from clarifytrial.settings import EpisodeSettings
from clarifytrial.trace import TraceRecorder
from clarifytrial.workflow import (
    EpisodeAgents,
    PatientScreeningCase,
    PatientScreeningRunner,
    PatientScreeningStopReason,
    ScreeningTrial,
    WorkflowProtocolError,
)


def _fact(evidence_id: str, concept: str, value: float) -> EvidenceFact:
    return EvidenceFact(
        evidence_id=evidence_id,
        statement=f"합성 {concept} 값은 {value:g}이다.",
        source_type=EvidenceSourceType.OFFICIAL_VERIFICATION,
        source_location=f"synthetic-record#{evidence_id}",
        event_date=date(2026, 8, 20),
        recorded_date=date(2026, 8, 20),
        verification_status=VerificationStatus.VERIFIED,
        concept=concept,
        value=value,
        unit="score",
    )


def _trial(trial_id: str, concept: str) -> ScreeningTrial:
    return ScreeningTrial(
        trial_id=trial_id,
        criteria=[
            TrialCriterion(
                criterion_id=f"{trial_id}-criterion",
                trial_id=trial_id,
                kind="inclusion",
                statement=f"{concept} 값이 1 이상이어야 한다.",
                source_location=f"protocol:{trial_id}#criterion",
                numeric_constraint=NumericConstraint(
                    concept=concept,
                    operator=ComparisonOperator.GTE,
                    threshold=1,
                    unit="score",
                ),
            )
        ],
    )


def _case() -> PatientScreeningCase:
    requests = [
        NextEvidenceRequest(
            fact_id="recent-b",
            description="B 조건을 확인할 기존 공식 결과",
            related_criterion_ids=["TRIAL-B-criterion"],
            acceptable_actions=["REQUEST_VERIFICATION"],
            reason="현재 B 조건을 확인하려면 이 결과가 필요하다.",
        ),
        NextEvidenceRequest(
            fact_id="recent-d",
            description="D 조건을 확인할 새 검사 결과",
            related_criterion_ids=["TRIAL-D-criterion"],
            acceptable_actions=["REQUEST_VERIFICATION"],
            reason="현재 D 조건을 확인하려면 새 확인이 필요하다.",
        ),
    ]
    return PatientScreeningCase(
        case_id="synthetic-multi-trial",
        disease_group="synthetic-test",
        trials=[
            _trial("TRIAL-A", "marker_a"),
            _trial("TRIAL-B", "marker_b"),
            _trial("TRIAL-C", "marker_c"),
            _trial("TRIAL-D", "marker_d"),
        ],
        initial_patient_state=PatientState(
            patient_id="SYNTHETIC-001",
            as_of=datetime(2026, 8, 21, tzinfo=timezone.utc),
            facts=[
                _fact("known-a", "marker_a", 1),
                _fact("known-c", "marker_c", 0),
            ],
        ),
        evidence_requests=requests,
        acquisition_options=[
            AcquisitionOption(
                option_id="recent-b:existing-result",
                fact_id="recent-b",
                action="REQUEST_VERIFICATION",
                acquisition_mode=AcquisitionMode.EXISTING_OFFICIAL_RESULT,
                available_now=True,
                expected_delay_hours=4,
                visit_required=False,
                direct_cost_band=DirectCostBand.NONE,
                physical_burden_0_to_3=0,
                emotional_burden_0_to_3=0,
                medical_risk_0_to_3=0,
                treatment_disruption_0_to_3=0,
                source_note="합성 기존 결과 경로",
            ),
            AcquisitionOption(
                option_id="recent-d:new-test",
                fact_id="recent-d",
                action="REQUEST_VERIFICATION",
                acquisition_mode=AcquisitionMode.NEW_NONINVASIVE_TEST,
                available_now=True,
                expected_delay_hours=48,
                visit_required=True,
                direct_cost_band=DirectCostBand.MEDIUM,
                physical_burden_0_to_3=1,
                emotional_burden_0_to_3=1,
                medical_risk_0_to_3=1,
                treatment_disruption_0_to_3=0,
                new_test_required=True,
                requires_patient_choice=True,
                requires_clinician_authorization=True,
                source_note="합성 새 검사 경로",
            ),
        ],
    )


def _tools(case: PatientScreeningCase) -> SyntheticInformationTools:
    requests = [
        PublicFactRequest(
            fact_id=item.fact_id,
            description=item.description,
            available_actions=tuple(item.acceptable_actions),
        )
        for item in case.evidence_requests
    ]
    answers = [
        HiddenFactAnswer(
            fact_id="recent-b",
            access_path="REQUEST_VERIFICATION",
            evidence=_fact("revealed-b", "marker_b", 1),
        ),
        HiddenFactAnswer(
            fact_id="recent-d",
            access_path="REQUEST_VERIFICATION",
            evidence=_fact("revealed-d", "marker_d", 1),
        ),
    ]
    return SyntheticInformationTools(
        PublicQuestionCatalog(requests), HiddenPatientEnvironment(answers)
    )


def _model(
    *,
    change_selected_action: bool = False,
    return_one_assessment_per_call: bool = False,
) -> ScriptedStructuredModel:
    def coordinate(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        return {
            "route": payload["allowed_routes"][0],
            "target_ids": payload["required_target_ids"],
            "reason_code": "code_allowed_transition",
            "reason": "코드가 허용한 다음 단계와 대상을 그대로 따른다.",
        }

    def match(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        requests = payload["evidence_requests"]
        assessments = []
        for criterion in payload["criteria"]:
            criterion_id = criterion["criterion_id"]
            checked = payload["mechanical_checks"][criterion_id]
            missing = []
            if checked["evidence_sufficiency"] != "sufficient":
                missing = [
                    item["fact_id"]
                    for item in requests
                    if criterion_id in item["related_criterion_ids"]
                ]
            assessments.append(
                {
                    "criterion_id": criterion_id,
                    "criterion_source_location": criterion["source_location"],
                    "clinical_status": checked["clinical_status"],
                    "evidence_sufficiency": checked["evidence_sufficiency"],
                    "evidence_ids": checked["evidence_ids"],
                    "missing_information_ids": missing,
                    "rationale": "구조화된 값과 공개 조건을 비교했다.",
                    "review_flags": [],
                }
            )
        return {
            "assessments": (
                assessments[:1]
                if return_one_assessment_per_call
                else assessments
            )
        }

    def write_request(payload: Mapping[str, Any]) -> Mapping[str, Any]:
        required = dict(payload["required_action"])
        if change_selected_action:
            required["target_fact_id"] = "recent-d"
        return {
            **required,
            "reason": "코드가 고른 확인 경로를 환자에게 설명한다.",
            "message": "기존 결과를 확인하거나 필요한 공식 확인 절차를 안내해 주세요.",
        }

    return ScriptedStructuredModel(
        {
            "coordinator": coordinate,
            "matcher_judge": match,
            "next_evidence": write_request,
            "selective_reviewer": lambda _: pytest.fail(
                "this synthetic flow has no review defect"
            ),
        }
    )


def _agents(model: ScriptedStructuredModel) -> EpisodeAgents:
    return EpisodeAgents(
        coordinator=CoordinatorAgent(model),
        matcher_judge=MatcherJudgeAgent(model),
        next_evidence=NextEvidenceAgent(model),
        selective_reviewer=SelectiveReviewerAgent(model),
    )


def test_patient_workflow_connects_agents_burden_rules_and_two_output_views() -> None:
    case = _case()
    model = _model()
    trace = TraceRecorder(case.case_id)
    result = PatientScreeningRunner(
        _agents(model),
        EpisodeSettings(
            max_external_actions=2,
            max_selective_reviews=0,
            max_cycles=8,
        ),
    ).run(case, _tools(case), trace=trace)

    assert (
        result.stop_reason
        is PatientScreeningStopReason.AWAITING_PATIENT_CHOICE
    )
    by_trial = {item.trial_id: item for item in result.final_decisions}
    assert by_trial["TRIAL-A"].confirmation_status.value == "confirmed"
    assert by_trial["TRIAL-B"].confirmation_status.value == "confirmed"
    assert by_trial["TRIAL-C"].confirmation_status.value == "ineligible"
    assert by_trial["TRIAL-D"].confirmation_status.value == "not_confirmed"

    assert [item.agent_action.target_fact_id for item in result.action_history] == [
        "recent-b"
    ]
    assert result.planned_action is not None
    assert result.planned_action.target_fact_id == "recent-d"
    assert "revealed-b" in {
        item.evidence_id for item in result.final_patient_state.facts
    }
    assert "revealed-d" not in {
        item.evidence_id for item in result.final_patient_state.facts
    }

    views = result.guidance.recommendation_views
    assert [item.trial_id for item in views.current_evidence.trials] == [
        "TRIAL-A",
        "TRIAL-B",
    ]
    assert [item.trial_id for item in views.broader_review.trials] == [
        "TRIAL-A",
        "TRIAL-B",
        "TRIAL-D",
    ]
    pending = views.broader_review.trials[-1]
    assert [item.fact_id for item in pending.missing_information] == ["recent-d"]
    assert pending.missing_information[0].confirmation_methods == [
        "공식 검사 결과 또는 의료진 확인"
    ]
    assert result.guidance.patient_message.request_message
    assert "추가 확인 후보를 참가 가능으로 확정한 것은 아닙니다" in (
        views.broader_review.explanation
    )
    assert len(result.ineligible_boundary_differences) == 1
    boundary = result.ineligible_boundary_differences[0]
    assert boundary.trial_id == "TRIAL-C"
    assert boundary.current_value == 0
    assert boundary.threshold == 1
    assert boundary.difference_from_threshold == -1
    assert boundary.absolute_difference == 1
    assert boundary.position.value == "below"
    assert "기준보다 1 score 낮습니다" in boundary.explanation
    assert any(item.actor == "information_planning_rules" for item in trace.events)
    assert model.call_count == {
        "next_evidence": 2,
    }
    assert model.call_count.get("matcher_judge", 0) == 0
    assert sum(item.actor == "coordinator_rules" for item in trace.events) == 5


def test_unavailable_answer_does_not_block_other_missing_facts() -> None:
    case = _case()
    options = [
        item.model_copy(
            update={
                "acquisition_mode": AcquisitionMode.EXISTING_OFFICIAL_RESULT,
                "new_test_required": False,
                "requires_patient_choice": False,
                "requires_clinician_authorization": False,
            }
        )
        for item in case.acquisition_options
    ]
    case = case.model_copy(update={"acquisition_options": options})
    requests = [
        PublicFactRequest(
            fact_id=item.fact_id,
            description=item.description,
            available_actions=tuple(item.acceptable_actions),
        )
        for item in case.evidence_requests
    ]
    tools = SyntheticInformationTools(
        PublicQuestionCatalog(requests),
        HiddenPatientEnvironment(
            [
                HiddenFactAnswer(
                    fact_id="recent-d",
                    access_path="REQUEST_VERIFICATION",
                    evidence=_fact("revealed-d", "marker_d", 1),
                )
            ]
        ),
    )

    result = PatientScreeningRunner(
        _agents(_model()),
        EpisodeSettings(
            max_external_actions=3,
            max_selective_reviews=0,
            max_cycles=12,
        ),
    ).run(case, tools)

    assert [item.agent_action.target_fact_id for item in result.action_history] == [
        "recent-b",
        "recent-d",
    ]
    assert result.action_history[0].tool_result.status.value == "not_available"
    assert result.action_history[1].tool_result.status.value == "revealed"
    by_trial = {item.trial_id: item for item in result.final_decisions}
    assert by_trial["TRIAL-D"].confirmation_status.value == "confirmed"
    assert result.stop_reason is PatientScreeningStopReason.DEFERRED
    assert result.guidance.selected_option is None
    assert any(
        item.option_id.startswith("recent-b")
        and "얻지 못한" in item.reason
        for item in result.guidance.decision_trace.removed_options
    )


def test_large_action_runs_only_after_both_approvals() -> None:
    case = _case()
    runner = PatientScreeningRunner(
        _agents(_model()),
        EpisodeSettings(
            max_external_actions=2,
            max_selective_reviews=0,
            max_cycles=8,
        ),
    )
    first = runner.run(case, _tools(case))
    assert first.stop_reason is PatientScreeningStopReason.AWAITING_PATIENT_CHOICE
    option_id = first.guidance.selected_option.option_id

    second = runner.run(
        case.model_copy(update={"initial_patient_state": first.final_patient_state}),
        _tools(case),
        initial_revealed_fact_ids={"recent-b"},
        patient_approved_option_ids={option_id},
    )
    assert (
        second.stop_reason
        is PatientScreeningStopReason.AWAITING_CLINICIAN_AUTHORIZATION
    )

    third = runner.run(
        case.model_copy(update={"initial_patient_state": first.final_patient_state}),
        _tools(case),
        initial_revealed_fact_ids={"recent-b"},
        patient_approved_option_ids={option_id},
        clinician_authorized_option_ids={option_id},
    )
    assert [item.agent_action.target_fact_id for item in third.action_history] == [
        "recent-d"
    ]
    assert third.stop_reason is PatientScreeningStopReason.ALL_TRIALS_RESOLVED


def test_message_agent_cannot_replace_the_code_selected_fact() -> None:
    case = _case()
    model = _model(change_selected_action=True)
    runner = PatientScreeningRunner(
        _agents(model),
        EpisodeSettings(
            max_external_actions=2,
            max_selective_reviews=0,
            max_cycles=8,
        ),
    )

    with pytest.raises(
        WorkflowProtocolError,
        match="cannot change the fact, acquisition path, or related criteria",
    ):
        runner.run(case, _tools(case))


def test_large_criterion_bundle_is_chunked_and_missing_rows_are_retried() -> None:
    base_case = _case()
    case = base_case.model_copy(
        update={
            "trials": [
                trial.model_copy(
                    update={
                        "criteria": [
                            criterion.model_copy(
                                update={"numeric_constraint": None}
                            )
                            for criterion in trial.criteria
                        ]
                    }
                )
                for trial in base_case.trials
            ]
        }
    )
    model = _model(return_one_assessment_per_call=True)
    result = PatientScreeningRunner(
        _agents(model),
        EpisodeSettings(
            max_external_actions=0,
            max_selective_reviews=0,
            max_cycles=4,
            criterion_batch_size=2,
        ),
    ).run(case, _tools(case))

    assert len(result.final_decisions) == 4
    # Two initial chunks return one of two requested rows.  Each missing row is
    # then requested alone, while the two successful rows are preserved.
    assert model.call_count["matcher_judge"] == 4
