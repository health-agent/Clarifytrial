from clarifytrial.contracts import (
    AgentAction,
    CandidateStatus,
    ClinicalStatus,
    ConfirmationStatus,
    CriterionAssessment,
    EvidenceSufficiency,
    NextAction,
    TrialDecision,
)
from clarifytrial.evaluation import CriterionGold, DecisionGold, score_decision


def _prediction() -> TrialDecision:
    return TrialDecision(
        trial_id="NCT-SYNTHETIC-001",
        candidate_status=CandidateStatus.RETAIN,
        confirmation_status=ConfirmationStatus.NOT_CONFIRMED,
        criterion_assessments=[
            CriterionAssessment(
                criterion_id="platelets",
                criterion_source_location="protocol#inclusion-4",
                clinical_status=ClinicalStatus.SUPPORTS,
                evidence_sufficiency=EvidenceSufficiency.INSUFFICIENT,
                evidence_ids=["old-lab"],
                missing_information_ids=["fresh-platelets"],
                rationale="The old value supports eligibility but is outside 14 days.",
            )
        ],
        next_action=AgentAction(
            action=NextAction.REQUEST_VERIFICATION,
            target_fact_id="fresh-platelets",
            related_criterion_ids=["platelets"],
            reason="A current official result can complete the criterion.",
            message="Request a platelet result collected within the last 14 days.",
        ),
    )


def test_scores_two_trial_labels_and_action_separately() -> None:
    gold = DecisionGold(
        candidate_status=CandidateStatus.RETAIN,
        confirmation_status=ConfirmationStatus.NOT_CONFIRMED,
        criteria=[
            CriterionGold(
                criterion_id="platelets",
                clinical_status=ClinicalStatus.SUPPORTS,
                evidence_sufficiency=EvidenceSufficiency.INSUFFICIENT,
                evidence_ids=["old-lab"],
            )
        ],
        acceptable_actions=[
            NextAction.REQUEST_VERIFICATION,
            NextAction.LOOKUP_RECORD,
        ],
        acceptable_target_fact_ids=["fresh-platelets"],
    )

    score = score_decision(_prediction(), gold)

    assert score.candidate_correct
    assert score.confirmation_correct
    assert score.action_acceptable
    assert score.target_fact_acceptable
    assert score.fully_correct


def test_wrong_confirmation_does_not_hide_correct_candidate_retention() -> None:
    prediction = _prediction().model_copy(
        update={"confirmation_status": ConfirmationStatus.CONFIRMED}
    )
    gold = DecisionGold(
        candidate_status=CandidateStatus.RETAIN,
        confirmation_status=ConfirmationStatus.NOT_CONFIRMED,
        criteria=[],
        acceptable_actions=[NextAction.REQUEST_VERIFICATION],
        acceptable_target_fact_ids=["fresh-platelets"],
    )

    score = score_decision(prediction, gold)

    assert score.candidate_correct
    assert not score.confirmation_correct
    assert not score.fully_correct
