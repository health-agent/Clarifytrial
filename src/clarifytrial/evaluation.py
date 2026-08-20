"""Common scoring for criterion, trial, and next-action outputs.

Gold labels live in evaluation inputs, never in an agent prompt or retrieval
index.  The scorer therefore accepts predictions and gold objects separately.
"""

from __future__ import annotations

from pydantic import Field, model_validator

from .contracts import (
    CandidateStatus,
    ClinicalStatus,
    ConfirmationStatus,
    ContractModel,
    EvidenceSufficiency,
    NextAction,
    TrialDecision,
)


class CriterionGold(ContractModel):
    """Reference labels for one criterion at one visible patient state."""

    criterion_id: str = Field(min_length=1)
    clinical_status: ClinicalStatus
    evidence_sufficiency: EvidenceSufficiency
    evidence_ids: list[str] = Field(default_factory=list)


class DecisionGold(ContractModel):
    """Reference labels that may allow more than one safe next action."""

    candidate_status: CandidateStatus
    confirmation_status: ConfirmationStatus
    criteria: list[CriterionGold]
    acceptable_actions: list[NextAction] = Field(min_length=1)
    acceptable_target_fact_ids: list[str | None] = Field(min_length=1)

    @model_validator(mode="after")
    def labels_are_unique(self) -> "DecisionGold":
        criterion_ids = [item.criterion_id for item in self.criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("criteria must not contain duplicate criterion_id values")
        if len(self.acceptable_actions) != len(set(self.acceptable_actions)):
            raise ValueError("acceptable_actions must not contain duplicates")
        if len(self.acceptable_target_fact_ids) != len(
            set(self.acceptable_target_fact_ids)
        ):
            raise ValueError("acceptable_target_fact_ids must not contain duplicates")
        return self


class DecisionScore(ContractModel):
    """Atomic results retained for later aggregation across cases."""

    candidate_correct: bool
    confirmation_correct: bool
    action_acceptable: bool
    target_fact_acceptable: bool
    criterion_status_correct: int = Field(ge=0)
    criterion_sufficiency_correct: int = Field(ge=0)
    criterion_evidence_exact: int = Field(ge=0)
    criterion_total: int = Field(ge=0)
    missing_prediction_criterion_ids: list[str] = Field(default_factory=list)
    unexpected_prediction_criterion_ids: list[str] = Field(default_factory=list)

    @property
    def fully_correct(self) -> bool:
        return (
            self.candidate_correct
            and self.confirmation_correct
            and self.action_acceptable
            and self.target_fact_acceptable
            and self.criterion_status_correct == self.criterion_total
            and self.criterion_sufficiency_correct == self.criterion_total
            and self.criterion_evidence_exact == self.criterion_total
            and not self.missing_prediction_criterion_ids
            and not self.unexpected_prediction_criterion_ids
        )


def score_decision(prediction: TrialDecision, gold: DecisionGold) -> DecisionScore:
    """Compare one trial decision without collapsing its distinct labels."""

    predicted_by_id = {
        assessment.criterion_id: assessment
        for assessment in prediction.criterion_assessments
    }
    gold_by_id = {criterion.criterion_id: criterion for criterion in gold.criteria}

    shared_ids = sorted(predicted_by_id.keys() & gold_by_id.keys())
    status_correct = 0
    sufficiency_correct = 0
    evidence_exact = 0
    for criterion_id in shared_ids:
        predicted = predicted_by_id[criterion_id]
        expected = gold_by_id[criterion_id]
        status_correct += predicted.clinical_status is expected.clinical_status
        sufficiency_correct += (
            predicted.evidence_sufficiency is expected.evidence_sufficiency
        )
        evidence_exact += set(predicted.evidence_ids) == set(expected.evidence_ids)

    return DecisionScore(
        candidate_correct=prediction.candidate_status is gold.candidate_status,
        confirmation_correct=(
            prediction.confirmation_status is gold.confirmation_status
        ),
        action_acceptable=prediction.next_action.action in gold.acceptable_actions,
        target_fact_acceptable=(
            prediction.next_action.target_fact_id
            in gold.acceptable_target_fact_ids
        ),
        criterion_status_correct=status_correct,
        criterion_sufficiency_correct=sufficiency_correct,
        criterion_evidence_exact=evidence_exact,
        criterion_total=len(gold.criteria),
        missing_prediction_criterion_ids=sorted(
            gold_by_id.keys() - predicted_by_id.keys()
        ),
        unexpected_prediction_criterion_ids=sorted(
            predicted_by_id.keys() - gold_by_id.keys()
        ),
    )
