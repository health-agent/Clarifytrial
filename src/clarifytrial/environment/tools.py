"""Public question catalogue and controlled synthetic information tools."""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..contracts import AgentAction, EvidenceFact, NextAction, PatientState
from .hidden_patient import (
    EnvironmentResponse,
    EnvironmentStatus,
    HiddenPatientEnvironment,
)


_INFORMATION_PATHS = frozenset(
    {
        NextAction.LOOKUP_RECORD,
        NextAction.ASK_PATIENT,
        NextAction.REQUEST_VERIFICATION,
    }
)


class PublicFactRequest(BaseModel):
    """Information an agent may use when choosing its next action.

    The class deliberately has no value, expected answer, or evaluation label.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    available_actions: tuple[NextAction, ...] = Field(min_length=1)

    @field_validator("available_actions")
    @classmethod
    def actions_are_information_paths(
        cls,
        actions: tuple[NextAction, ...],
    ) -> tuple[NextAction, ...]:
        if len(set(actions)) != len(actions):
            raise ValueError("available_actions must not contain duplicates")
        invalid = set(actions) - _INFORMATION_PATHS
        if invalid:
            names = ", ".join(sorted(action.value for action in invalid))
            raise ValueError(f"not an information path: {names}")
        return actions


class PublicQuestionCatalog:
    """Read-only descriptions and allowed paths exposed to an agent."""

    def __init__(self, requests: Iterable[PublicFactRequest]) -> None:
        self._requests: dict[str, PublicFactRequest] = {}
        for request in requests:
            if request.fact_id in self._requests:
                raise ValueError(f"duplicate public fact_id: {request.fact_id!r}")
            self._requests[request.fact_id] = request

    def list_requests(self) -> tuple[PublicFactRequest, ...]:
        """Return public request descriptions in their authored order."""

        return tuple(self._requests.values())

    def allows(self, fact_id: str, action: NextAction) -> bool:
        """Check a route without consulting or revealing the hidden answers."""

        request = self._requests.get(fact_id)
        return request is not None and action in request.available_actions


class ToolExecutionResult(BaseModel):
    """One tool result and the resulting public patient state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: NextAction
    target_fact_id: str | None = None
    status: EnvironmentStatus
    new_facts: list[EvidenceFact] = Field(default_factory=list)
    patient_state: PatientState


class SyntheticInformationTools:
    """Validate an agent action publicly, then call the private environment."""

    def __init__(
        self,
        catalog: PublicQuestionCatalog,
        environment: HiddenPatientEnvironment,
    ) -> None:
        self._catalog = catalog
        self._environment = environment

    def public_requests(self) -> tuple[PublicFactRequest, ...]:
        """Return the complete information menu that may be shown to an agent."""

        return self._catalog.list_requests()

    def execute(
        self,
        agent_action: AgentAction,
        patient_state: PatientState,
    ) -> ToolExecutionResult:
        """Execute one action without exposing the environment's answer table."""

        action = agent_action.action
        fact_id = agent_action.target_fact_id

        if action in _INFORMATION_PATHS and (
            fact_id is None or not self._catalog.allows(fact_id, action)
        ):
            response = EnvironmentResponse(
                action=action,
                target_fact_id=fact_id,
                status=EnvironmentStatus.NOT_AVAILABLE,
            )
        else:
            response = self._environment.execute(action, fact_id)

        updated_state = patient_state
        if response.new_facts:
            updated_state = patient_state.model_copy(
                update={"facts": [*patient_state.facts, *response.new_facts]}
            )

        return ToolExecutionResult(
            action=response.action,
            target_fact_id=response.target_fact_id,
            status=response.status,
            new_facts=response.new_facts,
            patient_state=updated_state,
        )
