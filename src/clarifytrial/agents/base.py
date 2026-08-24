"""Common boundary for inspectable, role-specific model calls."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, ClassVar, Generic, Mapping, TypeVar, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..contracts import CriterionAssessment
from ..llm.base import ModelCall, ModelUsage, StructuredModel
from ..trace import TraceRecorder


ResponseT = TypeVar("ResponseT", bound=BaseModel)


class CoordinatorRoute(StrEnum):
    """One next step selected by the coordinator."""

    MATCHER_JUDGE = "MATCHER_JUDGE"
    NEXT_EVIDENCE = "NEXT_EVIDENCE"
    SELECTIVE_REVIEWER = "SELECTIVE_REVIEWER"
    FINISH = "FINISH"


class CoordinatorDecision(BaseModel):
    """Inspectable routing decision; it contains no clinical re-judgment."""

    model_config = ConfigDict(extra="forbid")

    route: CoordinatorRoute
    target_ids: list[str] = Field(default_factory=list)
    reason_code: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class CriterionAssessmentBatch(BaseModel):
    """All related criterion judgments returned for one candidate trial."""

    model_config = ConfigDict(extra="forbid")

    assessments: list[CriterionAssessment] = Field(min_length=1)

    @field_validator("assessments")
    @classmethod
    def criterion_ids_are_unique(
        cls, value: list[CriterionAssessment]
    ) -> list[CriterionAssessment]:
        criterion_ids = [assessment.criterion_id for assessment in value]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("assessments must not repeat a criterion_id")
        return value


class ReviewOutcome(StrEnum):
    """Allowed outcomes of the independent selective review."""

    APPROVE = "approve"
    REJUDGE = "rejudge"
    REQUEST_MORE_EVIDENCE = "request_more_evidence"
    HUMAN_REVIEW = "human_review"


class ReviewDecision(BaseModel):
    """A bounded review result tied to the supplied source identifiers."""

    model_config = ConfigDict(extra="forbid")

    conclusion_id: str = Field(min_length=1)
    decision: ReviewOutcome
    patient_evidence_ids: list[str] = Field(default_factory=list)
    trial_evidence_ids: list[str] = Field(default_factory=list)
    affected_condition_ids: list[str] = Field(default_factory=list)
    missing_fact_ids: list[str] = Field(default_factory=list)
    reason_code: str = Field(min_length=1)
    reason: str = Field(min_length=1)


@dataclass(frozen=True, slots=True)
class AgentResult(Generic[ResponseT]):
    """Validated output and provider usage from one isolated model call."""

    output: ResponseT
    usage: ModelUsage


class StructuredAgent(Generic[ResponseT]):
    """Execute one role without retaining or forwarding conversation history."""

    agent_name: ClassVar[str]
    prompt_id: ClassVar[str]
    response_model: ClassVar[type[BaseModel]]

    def __init__(self, model: StructuredModel) -> None:
        self._model = model

    def run(
        self,
        payload: Mapping[str, Any] | BaseModel,
        *,
        trace: TraceRecorder | None = None,
        cycle: int = 0,
        input_refs: list[str] | None = None,
    ) -> AgentResult[ResponseT]:
        """Make one structured call from only the explicitly supplied payload."""

        if isinstance(payload, BaseModel):
            call_payload = payload.model_dump(mode="json")
        else:
            call_payload = dict(payload)

        response, usage = self._model.complete(
            ModelCall(
                role=self.agent_name,
                prompt_id=self.prompt_id,
                payload=call_payload,
                response_model=self.response_model,
            )
        )
        typed_response = cast(ResponseT, response)

        if trace is not None:
            response_trace = typed_response.model_dump(mode="json")
            if self.agent_name == "patient_record_structurer":
                # Patient source quotes, dates and values stay in the prepared
                # cited input files.  The general execution trace needs only
                # enough structure to audit the model boundary.
                facts = response_trace.get("facts", [])
                search_conditions = response_trace.get("search_conditions", [])
                response_trace = {
                    "search_condition_count": len(search_conditions),
                    "fact_count": len(facts),
                    "fact_keys": [item.get("fact_key") for item in facts],
                    "structured_value_fact_count": sum(
                        item.get("value") is not None for item in facts
                    ),
                }
            trace.record(
                cycle=cycle,
                actor=self.agent_name,
                event="structured_model_completed",
                input_refs=input_refs,
                output={
                    "prompt_id": self.prompt_id,
                    "response_model": self.response_model.__name__,
                    "response": response_trace,
                },
                usage=usage,
            )

        return AgentResult(output=typed_response, usage=usage)
