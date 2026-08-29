from datetime import datetime, timezone

from clarifytrial.contracts import (
    AgentAction,
    CandidateStatus,
    ClinicalStatus,
    ComparisonOperator,
    ConfirmationStatus,
    CriterionAssessment,
    CriterionKind,
    CriterionLogic,
    CriterionLogicOperator,
    EvidenceFact,
    EvidenceSourceType,
    EvidenceSufficiency,
    NextAction,
    NumericConstraint,
    PatientState,
    TrialCriterion,
    TrialDecision,
    VerificationStatus,
)
from clarifytrial.reporting import build_trial_reconsideration_summaries
from clarifytrial.reporting.terminal_summary import build_terminal_summary_lines
from clarifytrial.workflow import ScreeningTrial


def _leaf(criterion_id: str) -> CriterionLogic:
    return CriterionLogic(
        operator=CriterionLogicOperator.CRITERION,
        criterion_id=criterion_id,
    )


def _criterion(criterion_id: str, statement: str | None = None) -> TrialCriterion:
    return TrialCriterion(
        criterion_id=criterion_id,
        trial_id="TRIAL-1",
        kind=CriterionKind.INCLUSION,
        statement=statement or f"{criterion_id} 조건을 충족해야 한다.",
        source_location=f"synthetic#{criterion_id}",
    )


def _violation(criterion: TrialCriterion, evidence_id: str) -> CriterionAssessment:
    return CriterionAssessment(
        criterion_id=criterion.criterion_id,
        criterion_source_location=criterion.source_location,
        clinical_status=ClinicalStatus.VIOLATES,
        evidence_sufficiency=EvidenceSufficiency.SUFFICIENT,
        evidence_ids=[evidence_id],
        rationale="현재 값이 조건을 충족하지 않는다.",
    )


def _decision(assessments: list[CriterionAssessment]) -> TrialDecision:
    return TrialDecision(
        trial_id="TRIAL-1",
        candidate_status=CandidateStatus.REMOVE,
        confirmation_status=ConfirmationStatus.INELIGIBLE,
        criterion_assessments=assessments,
        next_action=AgentAction(
            action=NextAction.NONE,
            reason="현재 자료에서 제외 조건이 확인됐다.",
        ),
    )


def test_alternative_routes_keep_each_smallest_change_set() -> None:
    criteria = [_criterion("A"), _criterion("B"), _criterion("C")]
    logic = CriterionLogic(
        operator=CriterionLogicOperator.ANY,
        children=[
            CriterionLogic(
                operator=CriterionLogicOperator.ALL,
                children=[_leaf("A"), _leaf("B")],
            ),
            _leaf("C"),
        ],
    )
    trial = ScreeningTrial(
        trial_id="TRIAL-1",
        criteria=criteria,
        eligibility_logic=logic,
    )
    result = build_trial_reconsideration_summaries(
        patient_state=PatientState(
            patient_id="P1",
            as_of=datetime(2026, 8, 29, tzinfo=timezone.utc),
            facts=[],
        ),
        decisions=[
            _decision(
                [
                    _violation(criterion, f"E-{criterion.criterion_id}")
                    for criterion in criteria
                ]
            )
        ],
        trials=[trial],
    )

    assert len(result) == 1
    assert result[0].minimum_change_count == 1
    assert {frozenset(item.criterion_ids) for item in result[0].change_paths} == {
        frozenset({"A", "B"}),
        frozenset({"C"}),
    }


def test_verified_elapsed_period_produces_an_exact_recheck_date() -> None:
    criterion = TrialCriterion(
        criterion_id="WAIT-28",
        trial_id="TRIAL-1",
        kind=CriterionKind.EXCLUSION,
        statement="시술 후 28일 안인 사람은 제외한다.",
        source_location="synthetic#wait-28",
        numeric_constraint=NumericConstraint(
            concept="days_since_procedure",
            operator=ComparisonOperator.LT,
            threshold=28,
            unit="days",
        ),
    )
    assessment = _violation(criterion, "E-WAIT")
    state = PatientState(
        patient_id="P1",
        as_of=datetime(2026, 8, 29, tzinfo=timezone.utc),
        facts=[
            EvidenceFact(
                evidence_id="E-WAIT",
                statement="가상 환자는 시술 후 20일이 지났다.",
                source_type=EvidenceSourceType.SYNTHETIC_CASE,
                source_location="synthetic#elapsed",
                verification_status=VerificationStatus.VERIFIED,
                concept="days_since_procedure",
                value=20,
                unit="days",
            )
        ],
    )
    summary = build_trial_reconsideration_summaries(
        patient_state=state,
        decisions=[_decision([assessment])],
        trials=[ScreeningTrial(trial_id="TRIAL-1", criteria=[criterion])],
    )[0]

    assert summary.minimum_change_count == 1
    assert len(summary.recheck_dates) == 1
    recheck = summary.recheck_dates[0]
    assert recheck.days_remaining == 8
    assert recheck.recheck_date.isoformat() == "2026-09-06"
    assert "2026-09-06부터 다시 확인" in recheck.explanation


def test_terminal_summary_shows_change_path_and_recheck_date() -> None:
    result = {
        "screening": {
            "final_decisions": [
                {
                    "trial_id": "TRIAL-1",
                    "candidate_status": "remove",
                    "confirmation_status": "ineligible",
                    "criterion_assessments": [
                        {
                            "clinical_status": "violates",
                            "rationale": "시술 뒤 20일밖에 지나지 않았다.",
                        }
                    ],
                }
            ],
            "final_patient_state": {"facts": []},
            "decision_history": [],
            "stop_reason": "all_trials_resolved",
            "ineligible_boundary_differences": [],
            "trial_reconsideration_summaries": [
                {
                    "trial_id": "TRIAL-1",
                    "minimum_change_count": 1,
                    "change_paths": [
                        {
                            "criterion_statements": ["시술 후 28일 안이면 제외"],
                            "still_unconfirmed_statements": [],
                        }
                    ],
                    "recheck_dates": [
                        {
                            "explanation": (
                                "현재 20일이고 2026-09-06부터 다시 확인할 수 있습니다."
                            )
                        }
                    ],
                }
            ],
            "guidance": {"recommendation_views": None},
        },
        "usage": {"by_role": {}, "call_count": 0, "total_tokens": 0},
    }

    text = "\n".join(
        build_terminal_summary_lines(
            result,
            model_label="deterministic-workflow",
        )
    )
    assert "기록 또는 의료진 확인이 필요한 경로" in text
    assert "시술 후 28일 안이면 제외" in text
    assert "2026-09-06부터 다시 확인" in text


def test_age_above_a_pediatric_maximum_is_not_presented_as_recheckable() -> None:
    criterion = TrialCriterion(
        criterion_id="AGE-MAX",
        trial_id="TRIAL-1",
        kind=CriterionKind.INCLUSION,
        statement="Patients aged 5 to 15 years.",
        source_location="synthetic#age-max",
        numeric_constraint=NumericConstraint(
            concept="age",
            operator=ComparisonOperator.LTE,
            threshold=15,
            unit="years",
        ),
    )
    state = PatientState(
        patient_id="P1",
        as_of=datetime(2026, 8, 29, tzinfo=timezone.utc),
        facts=[
            EvidenceFact(
                evidence_id="E-AGE",
                statement="가상 환자는 34세다.",
                source_type=EvidenceSourceType.SYNTHETIC_CASE,
                source_location="synthetic#age",
                verification_status=VerificationStatus.VERIFIED,
                concept="age",
                value=34,
                unit="years",
            )
        ],
    )

    summary = build_trial_reconsideration_summaries(
        patient_state=state,
        decisions=[_decision([_violation(criterion, "E-AGE")])],
        trials=[ScreeningTrial(trial_id="TRIAL-1", criteria=[criterion])],
    )[0]

    path = summary.change_paths[0]
    assert path.reconsideration_status.value == "no_current_path"
    assert path.change_details[0].kind.value == "fixed_or_historical"
    assert "현재 다시 검토할 수 있는 경로는" in summary.explanation
