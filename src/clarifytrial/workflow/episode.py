"""Run the four agent roles through one bounded, inspectable episode."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import Field, model_validator

from ..agents import (
    CoordinatorAgent,
    CoordinatorRoute,
    MatcherJudgeAgent,
    NextEvidenceAgent,
    ReviewDecision,
    ReviewOutcome,
    SelectiveReviewerAgent,
)
from ..contracts import (
    AgentAction,
    ConfirmationStatus,
    ContractModel,
    CriterionAssessment,
    NextAction,
    NextEvidenceRequest,
    PatientState,
    TrialCriterion,
    TrialDecision,
)
from ..decision_rules import aggregate_trial_decision
from ..environment import (
    EnvironmentStatus,
    SyntheticInformationTools,
    ToolExecutionResult,
)
from ..settings import EpisodeSettings
from ..trace import TraceRecorder
from .trial_assessment import TrialAssessmentProtocolError, assess_trial_criteria


class WorkflowProtocolError(RuntimeError):
    """An agent returned a valid JSON object that violates the state machine."""


class EpisodeStopReason(StrEnum):
    CONFIRMED = "confirmed"
    INELIGIBLE = "ineligible"
    UNCERTAIN = "uncertain"
    NO_PENDING_INFORMATION = "no_pending_information"
    DEFERRED = "deferred"
    NO_USEFUL_ACTION = "no_useful_action"
    ACTION_LIMIT = "action_limit"
    HUMAN_REVIEW = "human_review"
    CYCLE_LIMIT = "cycle_limit"


class EpisodeCase(ContractModel):
    """Visible system input; it contains no hidden answers or gold labels."""

    case_id: str = Field(min_length=1)
    trial_id: str = Field(min_length=1)
    criteria: list[TrialCriterion] = Field(min_length=1)
    initial_patient_state: PatientState
    evidence_requests: list[NextEvidenceRequest] = Field(default_factory=list)

    @model_validator(mode="after")
    def references_are_consistent(self) -> "EpisodeCase":
        criterion_ids = [criterion.criterion_id for criterion in self.criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("criteria must not repeat a criterion_id")
        if any(criterion.trial_id != self.trial_id for criterion in self.criteria):
            raise ValueError("every criterion must belong to trial_id")
        known_ids = set(criterion_ids)
        request_ids = [request.fact_id for request in self.evidence_requests]
        if len(request_ids) != len(set(request_ids)):
            raise ValueError("evidence_requests must not repeat a fact_id")
        for request in self.evidence_requests:
            unknown = set(request.related_criterion_ids) - known_ids
            if unknown:
                raise ValueError(
                    f"request {request.fact_id!r} refers to unknown criteria: "
                    + ", ".join(sorted(unknown))
                )
        return self


class EpisodeResult(ContractModel):
    """Public outcome and intermediate decisions from one completed episode."""

    case_id: str
    stop_reason: EpisodeStopReason
    final_patient_state: PatientState
    final_decision: TrialDecision
    decision_history: list[TrialDecision]
    action_history: list[ToolExecutionResult]
    review_history: list[ReviewDecision]
    cycles: int = Field(ge=1)


@dataclass(frozen=True, slots=True)
class EpisodeAgents:
    coordinator: CoordinatorAgent
    matcher_judge: MatcherJudgeAgent
    next_evidence: NextEvidenceAgent
    selective_reviewer: SelectiveReviewerAgent


class EpisodeRunner:
    """Execute role calls while code owns budgets, transitions, and aggregation."""

    def __init__(self, agents: EpisodeAgents, settings: EpisodeSettings) -> None:
        self._agents = agents
        self._settings = settings

    def run(
        self,
        case: EpisodeCase,
        tools: SyntheticInformationTools,
        *,
        trace: TraceRecorder | None = None,
    ) -> EpisodeResult:
        recorder = trace or TraceRecorder(case.case_id)
        patient_state = case.initial_patient_state
        criteria_by_id = {
            criterion.criterion_id: criterion for criterion in case.criteria
        }
        request_by_id = {
            request.fact_id: request for request in case.evidence_requests
        }
        assessments: dict[str, CriterionAssessment] = {}
        dirty_ids: set[str] = set(criteria_by_id)
        attempted: list[ToolExecutionResult] = []
        reviews: list[ReviewDecision] = []
        history: list[TrialDecision] = []
        decision: TrialDecision | None = None
        review_resolved = False
        review_requested_ids: set[str] = set()
        forced_stop: EpisodeStopReason | None = None

        for cycle in range(self._settings.max_cycles):
            pending = self._pending_requests(
                assessments, request_by_id, review_requested_ids
            )
            allowed_routes = self._allowed_routes(
                dirty_ids=dirty_ids,
                decision=decision,
                pending=pending,
                action_count=len(attempted),
                review_count=len(reviews),
                review_resolved=review_resolved,
                forced_stop=forced_stop,
            )
            coordinator_payload = {
                "case_id": case.case_id,
                "cycle": cycle,
                "candidate_status": (
                    None if decision is None else decision.candidate_status.value
                ),
                "confirmation_status": (
                    None if decision is None else decision.confirmation_status.value
                ),
                "dirty_criterion_ids": sorted(dirty_ids),
                "pending_request_ids": [item.fact_id for item in pending],
                "review_required": bool(
                    decision is not None
                    and decision.review_required
                    and not review_resolved
                ),
                "remaining_external_actions": (
                    self._settings.max_external_actions - len(attempted)
                ),
                "remaining_selective_reviews": (
                    self._settings.max_selective_reviews - len(reviews)
                ),
                "allowed_routes": [route.value for route in allowed_routes],
                "required_target_ids": (
                    sorted(dirty_ids)
                    if allowed_routes == [CoordinatorRoute.MATCHER_JUDGE]
                    else []
                ),
            }
            route_result = self._agents.coordinator.run(
                coordinator_payload,
                trace=recorder,
                cycle=cycle,
                input_refs=[case.case_id],
            ).output
            selected_route = route_result.route
            if selected_route not in allowed_routes:
                allowed_text = ", ".join(route.value for route in allowed_routes)
                raise WorkflowProtocolError(
                    f"coordinator selected {selected_route.value}; "
                    f"allowed routes are {allowed_text}"
                )
            if (
                selected_route is CoordinatorRoute.MATCHER_JUDGE
                and set(route_result.target_ids) != dirty_ids
            ):
                raise WorkflowProtocolError(
                    "coordinator must route exactly the dirty criteria to matcher_judge"
                )

            if selected_route is CoordinatorRoute.MATCHER_JUDGE:
                target_ids = sorted(dirty_ids)
                try:
                    batch = assess_trial_criteria(
                        case_id=case.case_id,
                        trial_id=case.trial_id,
                        criteria=[criteria_by_id[item] for item in target_ids],
                        patient_state=patient_state,
                        evidence_requests=case.evidence_requests,
                        matcher_judge=self._agents.matcher_judge,
                        trace=recorder,
                        cycle=cycle,
                    )
                except TrialAssessmentProtocolError as error:
                    raise WorkflowProtocolError(str(error)) from error
                for assessment in batch:
                    assessments[assessment.criterion_id] = assessment

                dirty_ids.clear()
                decision = self._aggregate(
                    case, assessments, patient_state, recorder, cycle
                )
                history.append(decision)
                review_resolved = False
                continue

            if decision is None:
                raise WorkflowProtocolError("a non-matcher route needs a trial decision")

            if selected_route is CoordinatorRoute.SELECTIVE_REVIEWER:
                review_payload = self._review_payload(
                    case=case,
                    decision=decision,
                    patient_state=patient_state,
                )
                review = self._agents.selective_reviewer.run(
                    review_payload,
                    trace=recorder,
                    cycle=cycle,
                    input_refs=[
                        case.trial_id,
                        *[item.criterion_id for item in case.criteria],
                        *self._evidence_ids(patient_state),
                    ],
                ).output
                if review.conclusion_id != f"trial:{case.trial_id}":
                    raise WorkflowProtocolError(
                        "selective_reviewer returned an unknown conclusion_id"
                    )
                unknown_conditions = set(review.affected_condition_ids) - set(
                    criteria_by_id
                )
                if unknown_conditions:
                    raise WorkflowProtocolError(
                        "selective_reviewer returned unknown condition identifiers"
                    )
                unknown_facts = set(review.missing_fact_ids) - set(request_by_id)
                if unknown_facts:
                    raise WorkflowProtocolError(
                        "selective_reviewer returned unknown missing fact identifiers"
                    )
                reviews.append(review)
                if review.decision is ReviewOutcome.APPROVE:
                    review_resolved = True
                    decision = decision.model_copy(
                        update={"review_required": False, "review_reasons": []}
                    )
                    history.append(decision)
                elif review.decision is ReviewOutcome.REJUDGE:
                    dirty_ids.update(review.affected_condition_ids)
                    if not dirty_ids:
                        raise WorkflowProtocolError(
                            "rejudge requires at least one affected condition"
                        )
                elif review.decision is ReviewOutcome.REQUEST_MORE_EVIDENCE:
                    requested_ids = set(review.missing_fact_ids)
                    if not requested_ids:
                        raise WorkflowProtocolError(
                            "request_more_evidence requires a missing fact"
                        )
                    review_requested_ids.update(requested_ids)
                    review_resolved = True
                else:
                    forced_stop = EpisodeStopReason.HUMAN_REVIEW
                continue

            if selected_route is CoordinatorRoute.NEXT_EVIDENCE:
                action = self._agents.next_evidence.run(
                    {
                        "case_id": case.case_id,
                        "trial_id": case.trial_id,
                        "candidate_status": decision.candidate_status.value,
                        "confirmation_status": decision.confirmation_status.value,
                        "pending_information": [
                            item.model_dump(mode="json") for item in pending
                        ],
                        "attempted_actions": [
                            {
                                "action": item.action.value,
                                "target_fact_id": item.target_fact_id,
                                "status": item.status.value,
                            }
                            for item in attempted
                        ],
                        "remaining_external_actions": (
                            self._settings.max_external_actions - len(attempted)
                        ),
                    },
                    trace=recorder,
                    cycle=cycle,
                    input_refs=[item.fact_id for item in pending],
                ).output
                self._validate_action(action, pending)
                decision = decision.model_copy(update={"next_action": action})
                history.append(decision)

                if action.action is NextAction.DEFER:
                    forced_stop = EpisodeStopReason.DEFERRED
                    continue
                if action.action is NextAction.NONE:
                    forced_stop = EpisodeStopReason.NO_USEFUL_ACTION
                    continue

                result = tools.execute(action, patient_state)
                attempted.append(result)
                patient_state = result.patient_state
                recorder.record(
                    cycle=cycle,
                    actor="synthetic_information_tools",
                    event="information_action_completed",
                    input_refs=[action.target_fact_id or ""],
                    output={
                        "action": action.action.value,
                        "status": result.status.value,
                        "new_evidence_ids": [
                            fact.evidence_id for fact in result.new_facts
                        ],
                    },
                )
                if result.status is EnvironmentStatus.REVEALED:
                    review_requested_ids.discard(action.target_fact_id or "")
                    request = request_by_id[action.target_fact_id or ""]
                    dirty_ids.update(request.related_criterion_ids)
                continue

            stop_reason = self._stop_reason(
                decision=decision,
                pending=pending,
                action_count=len(attempted),
                forced_stop=forced_stop,
            )
            return EpisodeResult(
                case_id=case.case_id,
                stop_reason=stop_reason,
                final_patient_state=patient_state,
                final_decision=decision,
                decision_history=history,
                action_history=attempted,
                review_history=reviews,
                cycles=cycle + 1,
            )

        if decision is None:
            raise WorkflowProtocolError(
                "cycle limit was reached before any trial decision was produced"
            )
        return EpisodeResult(
            case_id=case.case_id,
            stop_reason=forced_stop or EpisodeStopReason.CYCLE_LIMIT,
            final_patient_state=patient_state,
            final_decision=decision,
            decision_history=history,
            action_history=attempted,
            review_history=reviews,
            cycles=self._settings.max_cycles,
        )

    def _allowed_routes(
        self,
        *,
        dirty_ids: set[str],
        decision: TrialDecision | None,
        pending: list[NextEvidenceRequest],
        action_count: int,
        review_count: int,
        review_resolved: bool,
        forced_stop: EpisodeStopReason | None,
    ) -> list[CoordinatorRoute]:
        if dirty_ids or decision is None:
            return [CoordinatorRoute.MATCHER_JUDGE]
        if forced_stop is not None:
            return [CoordinatorRoute.FINISH]
        routes: list[CoordinatorRoute] = []
        if (
            decision.review_required
            and not review_resolved
            and review_count < self._settings.max_selective_reviews
        ):
            routes.append(CoordinatorRoute.SELECTIVE_REVIEWER)
        if (
            pending
            and action_count < self._settings.max_external_actions
            and decision.confirmation_status
            not in {ConfirmationStatus.CONFIRMED, ConfirmationStatus.INELIGIBLE}
        ):
            routes.append(CoordinatorRoute.NEXT_EVIDENCE)
        return routes or [CoordinatorRoute.FINISH]

    @staticmethod
    def _pending_requests(
        assessments: dict[str, CriterionAssessment],
        request_by_id: dict[str, NextEvidenceRequest],
        review_requested_ids: set[str] | None = None,
    ) -> list[NextEvidenceRequest]:
        missing_ids = {
            fact_id
            for assessment in assessments.values()
            for fact_id in assessment.missing_information_ids
        }
        missing_ids.update(review_requested_ids or set())
        return [
            request
            for fact_id, request in request_by_id.items()
            if fact_id in missing_ids
        ]

    @staticmethod
    def _evidence_ids(patient_state: PatientState) -> list[str]:
        return [fact.evidence_id for fact in patient_state.facts]

    @staticmethod
    def _aggregate(
        case: EpisodeCase,
        assessments: dict[str, CriterionAssessment],
        patient_state: PatientState,
        recorder: TraceRecorder,
        cycle: int,
    ) -> TrialDecision:
        pending_ids = {
            fact_id
            for assessment in assessments.values()
            for fact_id in assessment.missing_information_ids
        }
        pending = [
            item for item in case.evidence_requests if item.fact_id in pending_ids
        ]
        decision = aggregate_trial_decision(
            trial_id=case.trial_id,
            criteria=case.criteria,
            assessments=list(assessments.values()),
            pending_information=pending,
            available_evidence_ids=EpisodeRunner._evidence_ids(patient_state),
        )
        recorder.record(
            cycle=cycle,
            actor="decision_rules",
            event="trial_decision_aggregated",
            input_refs=[item.criterion_id for item in case.criteria],
            output={
                "candidate_status": decision.candidate_status.value,
                "confirmation_status": decision.confirmation_status.value,
                "pending_information_ids": [item.fact_id for item in pending],
                "review_required": decision.review_required,
                "review_reasons": [item.value for item in decision.review_reasons],
            },
        )
        return decision

    @staticmethod
    def _validate_action(
        action: AgentAction,
        pending: list[NextEvidenceRequest],
    ) -> None:
        if action.action is NextAction.NONE:
            return
        request_by_id = {request.fact_id: request for request in pending}
        request = request_by_id.get(action.target_fact_id or "")
        if request is None:
            raise WorkflowProtocolError(
                "next_evidence selected a fact that is not currently pending"
            )
        if (
            action.action is not NextAction.DEFER
            and action.action not in request.acceptable_actions
        ):
            raise WorkflowProtocolError(
                "next_evidence selected a path not allowed for the target fact"
            )
        if not set(action.related_criterion_ids).issubset(
            request.related_criterion_ids
        ):
            raise WorkflowProtocolError(
                "next_evidence linked the action to unrelated criteria"
            )

    @staticmethod
    def _review_payload(
        *,
        case: EpisodeCase,
        decision: TrialDecision,
        patient_state: PatientState,
    ) -> dict[str, Any]:
        cited_ids = {
            evidence_id
            for assessment in decision.criterion_assessments
            for evidence_id in assessment.evidence_ids
        }
        return {
            "conclusion_id": f"trial:{case.trial_id}",
            "candidate_status": decision.candidate_status.value,
            "confirmation_status": decision.confirmation_status.value,
            "review_reasons": [item.value for item in decision.review_reasons],
            "criterion_assessments": [
                item.model_dump(mode="json")
                for item in decision.criterion_assessments
            ],
            "criteria": [item.model_dump(mode="json") for item in case.criteria],
            "patient_facts": [
                item.model_dump(mode="json")
                for item in patient_state.facts
                if item.evidence_id in cited_ids
            ],
            "as_of": patient_state.as_of.isoformat(),
        }

    def _stop_reason(
        self,
        *,
        decision: TrialDecision,
        pending: list[NextEvidenceRequest],
        action_count: int,
        forced_stop: EpisodeStopReason | None,
    ) -> EpisodeStopReason:
        if forced_stop is not None:
            return forced_stop
        if decision.review_required:
            return EpisodeStopReason.HUMAN_REVIEW
        if decision.confirmation_status is ConfirmationStatus.CONFIRMED:
            return EpisodeStopReason.CONFIRMED
        if decision.confirmation_status is ConfirmationStatus.INELIGIBLE:
            return EpisodeStopReason.INELIGIBLE
        if decision.confirmation_status is ConfirmationStatus.UNCERTAIN:
            return EpisodeStopReason.UNCERTAIN
        if action_count >= self._settings.max_external_actions and pending:
            return EpisodeStopReason.ACTION_LIMIT
        return EpisodeStopReason.NO_PENDING_INFORMATION
