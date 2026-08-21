"""Question-selection policies sharing one public input boundary."""

from __future__ import annotations

import random
from collections.abc import Mapping, Sequence
from typing import Protocol

from ..contracts import AgentAction, NextAction
from ..llm import ModelCall, StructuredModel
from ..llm.base import ModelUsage
from .contracts import InteractivePolicyView, InteractiveSnapshot


_ROUTE_COST = {
    NextAction.ASK_PATIENT: 1,
    NextAction.LOOKUP_RECORD: 2,
    NextAction.REQUEST_VERIFICATION: 3,
}


class QuestionPolicy(Protocol):
    policy_id: str

    @property
    def usage(self) -> Sequence[ModelUsage]: ...

    def select(
        self,
        view: InteractivePolicyView,
        snapshot: InteractiveSnapshot,
        revealed_fact_ids: frozenset[str],
    ) -> AgentAction: ...


class _DeterministicPolicy:
    policy_id = "deterministic"

    @property
    def usage(self) -> Sequence[ModelUsage]:
        return ()

    @staticmethod
    def _none(reason: str) -> AgentAction:
        return AgentAction(action=NextAction.NONE, reason=reason)

    @staticmethod
    def _action(view: InteractivePolicyView, fact_id: str, reason: str) -> AgentAction:
        public = next(
            item for item in view.available_information if item.fact_id == fact_id
        )
        action = public.available_actions[0]
        related = sorted(public.related_criterion_ids)
        message = None
        if action in {NextAction.ASK_PATIENT, NextAction.REQUEST_VERIFICATION}:
            message = public.description
        return AgentAction(
            action=action,
            target_fact_id=fact_id,
            related_criterion_ids=related,
            reason=reason,
            message=message,
        )


class NoQuestionPolicy(_DeterministicPolicy):
    policy_id = "no_questions"

    def select(self, case, snapshot, revealed_fact_ids):
        return self._none("추가 정보를 확인하지 않는 비교 방식이다.")


class AuthoredOrderPolicy(_DeterministicPolicy):
    policy_id = "authored_order"

    def select(self, case, snapshot, revealed_fact_ids):
        for public in case.available_information:
            if public.fact_id not in revealed_fact_ids:
                return self._action(
                    case,
                    public.fact_id,
                    "공개 목록에 적힌 순서대로 정보를 확인한다.",
                )
        return self._none("확인할 정보가 남아 있지 않다.")


class RandomQuestionPolicy(_DeterministicPolicy):
    policy_id = "random"

    def __init__(self, seed: int) -> None:
        self._seed = seed

    def select(self, case, snapshot, revealed_fact_ids):
        remaining = sorted(
            item.fact_id
            for item in case.available_information
            if item.fact_id not in revealed_fact_ids
        )
        if not remaining:
            return self._none("확인할 정보가 남아 있지 않다.")
        randomizer = random.Random(
            f"{self._seed}:{case.case_id}:{len(revealed_fact_ids)}"
        )
        fact_id = randomizer.choice(remaining)
        return self._action(case, fact_id, "남은 정보 중 하나를 무작위로 고른다.")


def _current_related_counts(
    case: InteractivePolicyView,
    snapshot: InteractiveSnapshot,
    revealed_fact_ids: frozenset[str],
) -> Mapping[str, tuple[int, int, int]]:
    decision_by_trial = {item.trial_id: item for item in snapshot.decisions}
    result: dict[str, tuple[int, int, int]] = {}
    criterion_to_trial = {
        criterion.criterion_id: trial.trial_id
        for trial in case.trials
        for criterion in trial.criteria
    }
    for public in case.available_information:
        fact_id = public.fact_id
        if fact_id in revealed_fact_ids:
            continue
        related_trials: set[str] = set()
        related_pending = 0
        for criterion_id in public.related_criterion_ids:
            trial_id = criterion_to_trial[criterion_id]
            decision = decision_by_trial[trial_id]
            if decision.confirmation_status.value in {"confirmed", "ineligible"}:
                continue
            related_trials.add(trial_id)
            related_pending += 1
        route = public.available_actions[0]
        result[fact_id] = (
            len(related_trials),
            related_pending,
            _ROUTE_COST[route],
        )
    return result


class WidestImpactPolicy(_DeterministicPolicy):
    policy_id = "widest_impact"

    def select(self, case, snapshot, revealed_fact_ids):
        counts = _current_related_counts(case, snapshot, revealed_fact_ids)
        useful = {key: value for key, value in counts.items() if value[0] > 0}
        if not useful:
            return self._none("현재 결정을 바꿀 수 있는 확인 항목이 없다.")
        fact_id = min(
            useful,
            key=lambda key: (-useful[key][0], -useful[key][1], key),
        )
        return self._action(
            case,
            fact_id,
            "현재 미해결 후보에 가장 넓게 영향을 주는 정보를 확인한다.",
        )


class ImpactCostPolicy(_DeterministicPolicy):
    policy_id = "impact_per_cost"

    def select(self, case, snapshot, revealed_fact_ids):
        counts = _current_related_counts(case, snapshot, revealed_fact_ids)
        useful = {key: value for key, value in counts.items() if value[0] > 0}
        if not useful:
            return self._none("현재 결정을 바꿀 수 있는 확인 항목이 없다.")
        fact_id = min(
            useful,
            key=lambda key: (
                -(useful[key][0] / useful[key][2]),
                -useful[key][1],
                key,
            ),
        )
        return self._action(
            case,
            fact_id,
            "미해결 후보에 미치는 영향과 확인 비용을 함께 고려한다.",
        )


class ClarifyTrialRulePolicy(_DeterministicPolicy):
    """The inspectable default policy for the first interactive benchmark."""

    policy_id = "clarifytrial_rule_v1"

    def select(self, case, snapshot, revealed_fact_ids):
        counts = _current_related_counts(case, snapshot, revealed_fact_ids)
        useful = {key: value for key, value in counts.items() if value[0] > 0}
        if not useful:
            return self._none("현재 결정을 바꿀 수 있는 확인 항목이 없다.")
        # Resolve more candidate trials first, then more criteria.  Route cost
        # breaks genuine ties; it never overrides a larger decision impact.
        fact_id = min(
            useful,
            key=lambda key: (
                -useful[key][0],
                -useful[key][1],
                useful[key][2],
                key,
            ),
        )
        return self._action(
            case,
            fact_id,
            "먼저 더 많은 후보와 조건을 해결하고, 동률이면 확인 부담이 낮은 경로를 고른다.",
        )


class ModelQuestionPolicy:
    """Use one structured model call per question without exposing answers."""

    policy_id = "sol_medium_question_selector_v1"

    def __init__(self, model: StructuredModel) -> None:
        self._model = model
        self._usage: list[ModelUsage] = []

    @property
    def usage(self) -> Sequence[ModelUsage]:
        return tuple(self._usage)

    def select(self, case, snapshot, revealed_fact_ids):
        counts = _current_related_counts(case, snapshot, revealed_fact_ids)
        remaining = []
        for public in case.available_information:
            fact_id = public.fact_id
            if fact_id in revealed_fact_ids:
                continue
            trial_count, criterion_count, cost = counts[fact_id]
            remaining.append(
                {
                    "fact_id": fact_id,
                    "description": public.description,
                    "available_actions": [
                        item.value for item in public.available_actions
                    ],
                    "related_criterion_ids": public.related_criterion_ids,
                    "currently_unresolved_related_trials": trial_count,
                    "currently_unresolved_related_criteria": criterion_count,
                    "route_cost": cost,
                }
            )
        payload = {
            "case_id": case.case_id,
            "disease_group": case.disease_group,
            "remaining_action_budget": case.action_budget - len(revealed_fact_ids),
            "current_trial_decisions": [
                {
                    "trial_id": item.trial_id,
                    "candidate_status": item.candidate_status.value,
                    "confirmation_status": item.confirmation_status.value,
                }
                for item in snapshot.decisions
            ],
            "available_information": remaining,
        }
        output, usage = self._model.complete(
            ModelCall(
                role="next_evidence",
                prompt_id="prompts/interactive_question_selector.md",
                payload=payload,
                response_model=AgentAction,
            )
        )
        self._usage.append(usage)
        if output.action is NextAction.NONE:
            return output
        allowed = {
            (item.fact_id, action)
            for item in case.available_information
            if item.fact_id not in revealed_fact_ids
            for action in item.available_actions
        }
        if (output.target_fact_id, output.action) not in allowed:
            raise ValueError("model selected an unavailable fact or route")
        return output
