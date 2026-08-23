"""Connect the multi-trial agents, burden policy, tools, and final report.

The loop in this module only coordinates named steps.  Typed contracts live in
``patient_screening_contracts``; deterministic decisions live in
``patient_screening_rules``; model-output checks live in
``patient_screening_agents``.
"""

from __future__ import annotations

from ..agents import (
    CoordinatorDecision,
    CoordinatorRoute,
    ReviewDecision,
    ReviewOutcome,
)
from ..contracts import AgentAction, CriterionAssessment, TrialDecision
from ..environment import EnvironmentStatus
from ..interactive.burden_contracts import (
    AcquisitionOption,
    AcquisitionPolicyId,
    ActionStatus,
)
from ..interactive.burden_policy import (
    build_patient_burden_profile,
    select_acquisition_option,
)
from ..settings import EpisodeSettings
from ..trace import TraceRecorder
from .episode import EpisodeAgents, WorkflowProtocolError
from .patient_screening_agents import (
    build_review_payload,
    validate_review,
    write_information_request,
)
from .patient_screening_contracts import (
    InformationTools,
    PatientScreeningActionRecord,
    PatientScreeningCase,
    PatientScreeningResult,
    PatientScreeningSnapshot,
    PatientScreeningStopReason,
)
from .patient_screening_rules import (
    aggregate_screening_trial,
    allowed_route,
    attach_action_to_decisions,
    build_policy_view,
    build_screening_result,
    group_acquisition_options,
    history_snapshot,
    interactive_snapshot,
    natural_stop_reason,
    no_action_decision,
    pending_requests,
)
from .trial_assessment import (
    TrialAssessmentProtocolError,
    assess_criteria_bundle,
    assess_trial_criteria,
)


class PatientScreeningRunner:
    """Run all supplied candidate trials through one shared patient state."""

    def __init__(self, agents: EpisodeAgents, settings: EpisodeSettings) -> None:
        self._agents = agents
        self._settings = settings

    def run(
        self,
        case: PatientScreeningCase,
        tools: InformationTools,
        *,
        trace: TraceRecorder | None = None,
        initial_revealed_fact_ids: set[str] | None = None,
        initial_unavailable_fact_ids: set[str] | None = None,
        patient_approved_option_ids: set[str] | None = None,
        clinician_authorized_option_ids: set[str] | None = None,
    ) -> PatientScreeningResult:
        recorder = trace or TraceRecorder(case.case_id)
        trials_by_id = {item.trial_id: item for item in case.trials}
        criterion_to_trial = {
            criterion.criterion_id: trial.trial_id
            for trial in case.trials
            for criterion in trial.criteria
        }
        criteria_by_id = {
            criterion.criterion_id: criterion
            for trial in case.trials
            for criterion in trial.criteria
        }
        request_by_id = {item.fact_id: item for item in case.evidence_requests}
        catalog = group_acquisition_options(case.acquisition_options)
        profile = build_patient_burden_profile(
            f"{case.case_id}:patient-input", case.patient_burden_input
        )
        view = build_policy_view(case, self._settings.max_external_actions)

        patient_state = case.initial_patient_state
        assessments: dict[str, dict[str, CriterionAssessment]] = {
            trial_id: {} for trial_id in trials_by_id
        }
        decisions: dict[str, TrialDecision] = {}
        dirty_ids = set(criteria_by_id)
        revealed_fact_ids: set[str] = set(initial_revealed_fact_ids or ())
        unavailable_fact_ids: set[str] = set(initial_unavailable_fact_ids or ())
        patient_approved = set(patient_approved_option_ids or ())
        clinician_authorized = set(clinician_authorized_option_ids or ())
        selected_options: list[AcquisitionOption] = []
        action_history: list[PatientScreeningActionRecord] = []
        review_history: list[ReviewDecision] = []
        review_requested_ids: set[str] = set()
        decision_history: list[PatientScreeningSnapshot] = []
        planned_action: AgentAction | None = None
        last_acquisition = no_action_decision(
            "조건 판단이 끝난 뒤 다음 확인 경로를 계산한다."
        )
        forced_stop: PatientScreeningStopReason | None = None

        for cycle in range(self._settings.max_cycles):
            pending = pending_requests(
                decisions, request_by_id, review_requested_ids
            )
            review_trial_ids = [
                item.trial_id for item in decisions.values() if item.review_required
            ]
            allowed_routes, required_targets = allowed_route(
                settings=self._settings,
                dirty_ids=dirty_ids,
                decisions=decisions,
                pending=pending,
                action_count=len(action_history),
                review_count=len(review_history),
                review_trial_ids=review_trial_ids,
                forced_stop=forced_stop,
            )
            coordinator_payload = {
                "case_id": case.case_id,
                "cycle": cycle,
                "trial_count": len(case.trials),
                "decision_count": len(decisions),
                "dirty_criterion_ids": sorted(dirty_ids),
                "pending_request_ids": [item.fact_id for item in pending],
                "review_trial_ids": sorted(review_trial_ids),
                "remaining_external_actions": (
                    self._settings.max_external_actions - len(action_history)
                ),
                "remaining_selective_reviews": (
                    self._settings.max_selective_reviews - len(review_history)
                ),
                "allowed_routes": [item.value for item in allowed_routes],
                "required_target_ids": required_targets,
            }
            if self._settings.use_model_coordinator:
                coordinator = self._agents.coordinator.run(
                    coordinator_payload,
                    trace=recorder,
                    cycle=cycle,
                    input_refs=[case.case_id, *required_targets],
                ).output
            else:
                coordinator = CoordinatorDecision(
                    route=allowed_routes[0],
                    target_ids=required_targets,
                    reason_code="single_allowed_route",
                    reason="코드 규칙상 가능한 다음 단계가 하나라 그대로 실행한다.",
                )
                recorder.record(
                    cycle=cycle,
                    actor="coordinator_rules",
                    event="single_route_selected",
                    input_refs=[case.case_id, *required_targets],
                    output={
                        "request": coordinator_payload,
                        "response": coordinator.model_dump(mode="json"),
                    },
                )
            if coordinator.route not in allowed_routes:
                raise WorkflowProtocolError(
                    f"coordinator selected {coordinator.route.value}; allowed route is "
                    f"{allowed_routes[0].value}"
                )
            if coordinator.target_ids != required_targets:
                raise WorkflowProtocolError(
                    "coordinator must return the required_target_ids unchanged"
                )

            if coordinator.route is CoordinatorRoute.MATCHER_JUDGE:
                if self._settings.batch_trial_judgments:
                    target = [
                        criterion
                        for trial in case.trials
                        for criterion in trial.criteria
                        if criterion.criterion_id in dirty_ids
                    ]
                    try:
                        updated = assess_criteria_bundle(
                            case_id=case.case_id,
                            criteria=target,
                            patient_state=patient_state,
                            evidence_requests=case.evidence_requests,
                            matcher_judge=self._agents.matcher_judge,
                            trace=recorder,
                            cycle=cycle,
                        )
                    except TrialAssessmentProtocolError as error:
                        raise WorkflowProtocolError(str(error)) from error
                    touched_trial_ids: set[str] = set()
                    for item in updated:
                        trial_id = criterion_to_trial[item.criterion_id]
                        assessments[trial_id][item.criterion_id] = item
                        touched_trial_ids.add(trial_id)
                    for trial_id in sorted(touched_trial_ids):
                        decisions[trial_id] = aggregate_screening_trial(
                            trial=trials_by_id[trial_id],
                            assessments=assessments[trial_id],
                            evidence_requests=case.evidence_requests,
                            patient_state=patient_state,
                        )
                else:
                    for trial_id in sorted(trials_by_id):
                        trial = trials_by_id[trial_id]
                        target = [
                            criterion
                            for criterion in trial.criteria
                            if criterion.criterion_id in dirty_ids
                        ]
                        if not target:
                            continue
                        try:
                            updated = assess_trial_criteria(
                                case_id=case.case_id,
                                trial_id=trial_id,
                                criteria=target,
                                patient_state=patient_state,
                                evidence_requests=case.evidence_requests,
                                matcher_judge=self._agents.matcher_judge,
                                trace=recorder,
                                cycle=cycle,
                            )
                        except TrialAssessmentProtocolError as error:
                            raise WorkflowProtocolError(str(error)) from error
                        assessments[trial_id].update(
                            {item.criterion_id: item for item in updated}
                        )
                        decisions[trial_id] = aggregate_screening_trial(
                            trial=trial,
                            assessments=assessments[trial_id],
                            evidence_requests=case.evidence_requests,
                            patient_state=patient_state,
                        )
                dirty_ids.clear()
                decision_history.append(
                    history_snapshot(cycle, "조건 판단과 상태 집계", decisions)
                )
                continue

            if coordinator.route is CoordinatorRoute.SELECTIVE_REVIEWER:
                trial_id = required_targets[0]
                decision = decisions[trial_id]
                review = self._agents.selective_reviewer.run(
                    build_review_payload(
                        case_id=case.case_id,
                        trial=trials_by_id[trial_id],
                        decision=decision,
                        patient_state=patient_state,
                    ),
                    trace=recorder,
                    cycle=cycle,
                    input_refs=[trial_id],
                ).output
                validate_review(
                    review,
                    trial_id=trial_id,
                    known_criterion_ids=set(criteria_by_id),
                    known_fact_ids=set(request_by_id),
                )
                review_history.append(review)
                if review.decision is ReviewOutcome.APPROVE:
                    decisions[trial_id] = decision.model_copy(
                        update={"review_required": False, "review_reasons": []}
                    )
                elif review.decision is ReviewOutcome.REJUDGE:
                    dirty_ids.update(review.affected_condition_ids)
                elif review.decision is ReviewOutcome.REQUEST_MORE_EVIDENCE:
                    review_requested_ids.update(review.missing_fact_ids)
                    added_requests = [
                        request_by_id[item] for item in review.missing_fact_ids
                    ]
                    combined_requests = {
                        item.fact_id: item
                        for item in [*decision.pending_information, *added_requests]
                    }
                    decisions[trial_id] = decision.model_copy(
                        update={
                            "review_required": False,
                            "review_reasons": [],
                            "pending_information": list(combined_requests.values()),
                        }
                    )
                else:
                    forced_stop = PatientScreeningStopReason.HUMAN_REVIEW
                decision_history.append(
                    history_snapshot(cycle, "선택 검토 결과 반영", decisions)
                )
                continue

            if coordinator.route is CoordinatorRoute.NEXT_EVIDENCE:
                snapshot = interactive_snapshot(patient_state, decisions)
                selection_catalog = catalog
                if self._settings.question_policy == "fixed_order":
                    fixed_fact_id = next(
                        (
                            item.fact_id
                            for item in pending
                            if item.fact_id not in revealed_fact_ids
                            and item.fact_id not in unavailable_fact_ids
                            and item.fact_id in catalog
                        ),
                        None,
                    )
                    selection_catalog = (
                        {}
                        if fixed_fact_id is None
                        else {fixed_fact_id: catalog[fixed_fact_id]}
                    )
                last_acquisition = select_acquisition_option(
                    view=view,
                    snapshot=snapshot,
                    revealed_fact_ids=frozenset(revealed_fact_ids),
                    unavailable_fact_ids=frozenset(unavailable_fact_ids),
                    catalog=selection_catalog,
                    profile=profile,
                    policy_id=AcquisitionPolicyId.PATIENT_ADAPTIVE,
                    selected_options=selected_options,
                )
                recorder.record(
                    cycle=cycle,
                    actor="information_planning_rules",
                    event="acquisition_path_selected",
                    input_refs=[item.fact_id for item in pending],
                    output=last_acquisition.model_dump(mode="json"),
                )
                selected = last_acquisition.selected_option
                if selected is None:
                    forced_stop = PatientScreeningStopReason.DEFERRED
                    continue
                request = request_by_id[selected.fact_id]
                planned_action = write_information_request(
                    next_evidence=self._agents.next_evidence,
                    case_id=case.case_id,
                    request=request,
                    selected=selected,
                    acquisition_decision=last_acquisition,
                    attempted=action_history,
                    trace=recorder,
                    cycle=cycle,
                )
                attach_action_to_decisions(
                    decisions, planned_action, criterion_to_trial
                )
                decision_history.append(
                    history_snapshot(cycle, "다음 확인 경로 선택", decisions)
                )
                patient_choice_needed = (
                    last_acquisition.action_status
                    is ActionStatus.AWAITING_PATIENT_CHOICE
                    or selected.requires_patient_choice
                )
                if patient_choice_needed and selected.option_id not in patient_approved:
                    last_acquisition = last_acquisition.model_copy(
                        update={"action_status": ActionStatus.AWAITING_PATIENT_CHOICE}
                    )
                    forced_stop = PatientScreeningStopReason.AWAITING_PATIENT_CHOICE
                    continue
                if (
                    selected.requires_clinician_authorization
                    and selected.option_id not in clinician_authorized
                ):
                    last_acquisition = last_acquisition.model_copy(
                        update={
                            "action_status": (
                                ActionStatus.AWAITING_CLINICIAN_AUTHORIZATION
                            )
                        }
                    )
                    forced_stop = (
                        PatientScreeningStopReason.AWAITING_CLINICIAN_AUTHORIZATION
                    )
                    continue
                last_acquisition = last_acquisition.model_copy(
                    update={"action_status": ActionStatus.RECOMMENDED}
                )

                tool_result = tools.execute(planned_action, patient_state)
                action_history.append(
                    PatientScreeningActionRecord(
                        step=len(action_history) + 1,
                        acquisition_decision=last_acquisition,
                        agent_action=planned_action,
                        tool_result=tool_result,
                    )
                )
                selected_options.append(selected)
                planned_action = None
                recorder.record(
                    cycle=cycle,
                    actor="information_tools",
                    event="information_action_completed",
                    input_refs=[selected.fact_id],
                    output=tool_result.model_dump(mode="json"),
                )
                if tool_result.status is not EnvironmentStatus.REVEALED:
                    unavailable_fact_ids.add(selected.fact_id)
                    planned_action = None
                    continue
                patient_state = tool_result.patient_state
                revealed_fact_ids.add(selected.fact_id)
                unavailable_fact_ids.discard(selected.fact_id)
                review_requested_ids.discard(selected.fact_id)
                dirty_ids.update(request.related_criterion_ids)
                continue

            stop_reason = forced_stop or natural_stop_reason(
                settings=self._settings,
                decisions=decisions,
                pending=pending,
                action_count=len(action_history),
            )
            return build_screening_result(
                case=case,
                patient_state=patient_state,
                decisions=decisions,
                history=decision_history,
                actions=action_history,
                reviews=review_history,
                planned_action=planned_action,
                acquisition_decision=last_acquisition,
                profile=profile,
                view=view,
                catalog=catalog,
                revealed_fact_ids=revealed_fact_ids,
                selected_options=selected_options,
                stop_reason=stop_reason,
                cycles=cycle + 1,
            )

        if not decisions:
            raise WorkflowProtocolError(
                "cycle limit was reached before any trial decision was produced"
            )
        return build_screening_result(
            case=case,
            patient_state=patient_state,
            decisions=decisions,
            history=decision_history,
            actions=action_history,
            reviews=review_history,
            planned_action=planned_action,
            acquisition_decision=last_acquisition,
            profile=profile,
            view=view,
            catalog=catalog,
            revealed_fact_ids=revealed_fact_ids,
            selected_options=selected_options,
            stop_reason=forced_stop or PatientScreeningStopReason.CYCLE_LIMIT,
            cycles=self._settings.max_cycles,
        )
