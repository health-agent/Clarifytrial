"""Private, deterministic answers for synthetic patient cases.

The workflow may select a fact and an information path, but it never receives
this environment's answer table as model input.  Answers are authored with the
synthetic case and returned verbatim; no language model generates patient data.
"""

from __future__ import annotations

from collections.abc import Iterable
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ..contracts import EvidenceFact, EvidenceSourceType, NextAction


_SOURCE_FOR_PATH = {
    NextAction.LOOKUP_RECORD: EvidenceSourceType.MEDICAL_RECORD,
    NextAction.ASK_PATIENT: EvidenceSourceType.PATIENT_REPORT,
    NextAction.REQUEST_VERIFICATION: EvidenceSourceType.OFFICIAL_VERIFICATION,
}


class HiddenFactAnswer(BaseModel):
    """One answer card stored only inside the synthetic environment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fact_id: str = Field(min_length=1)
    access_path: NextAction
    evidence: EvidenceFact

    @model_validator(mode="after")
    def source_matches_access_path(self) -> "HiddenFactAnswer":
        expected_source = _SOURCE_FOR_PATH.get(self.access_path)
        if expected_source is None:
            raise ValueError(
                "hidden answers require LOOKUP_RECORD, ASK_PATIENT, or "
                "REQUEST_VERIFICATION"
            )
        if self.evidence.source_type != expected_source:
            raise ValueError(
                f"{self.access_path.value} requires source_type "
                f"{expected_source.value}"
            )
        if self.evidence.event_date is None or self.evidence.recorded_date is None:
            raise ValueError(
                "hidden answers require both event_date and recorded_date"
            )
        return self


class EnvironmentStatus(str, Enum):
    """Observable outcome of one information action."""

    REVEALED = "revealed"
    ALREADY_REVEALED = "already_revealed"
    NOT_AVAILABLE = "not_available"
    NO_FACT = "no_fact"


class EnvironmentResponse(BaseModel):
    """Facts released by the environment for one requested action."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    action: NextAction
    target_fact_id: str | None = None
    status: EnvironmentStatus
    new_facts: list[EvidenceFact] = Field(default_factory=list)


class HiddenPatientEnvironment:
    """Keep synthetic answers private and reveal only the requested path.

    A fact can have more than one authored path, but it is released only once.
    Repeating the same fact is an explicit, successful no-op rather than a
    second copy of the evidence.
    """

    def __init__(self, answers: Iterable[HiddenFactAnswer]) -> None:
        self._answers: dict[tuple[NextAction, str], EvidenceFact] = {}
        self._revealed_fact_ids: set[str] = set()

        for answer in answers:
            key = (answer.access_path, answer.fact_id)
            if key in self._answers:
                raise ValueError(
                    "duplicate hidden answer for "
                    f"{answer.fact_id!r} through {answer.access_path.value}"
                )
            self._answers[key] = answer.evidence

    @property
    def revealed_fact_ids(self) -> frozenset[str]:
        """Return identifiers already released, never their hidden values."""

        return frozenset(self._revealed_fact_ids)

    def execute(
        self,
        action: NextAction,
        target_fact_id: str | None,
    ) -> EnvironmentResponse:
        """Return a pre-authored fact for an allowed information path."""

        if action in {NextAction.NONE, NextAction.DEFER}:
            return EnvironmentResponse(
                action=action,
                target_fact_id=target_fact_id,
                status=EnvironmentStatus.NO_FACT,
            )

        if action not in _SOURCE_FOR_PATH or not target_fact_id:
            return EnvironmentResponse(
                action=action,
                target_fact_id=target_fact_id,
                status=EnvironmentStatus.NOT_AVAILABLE,
            )

        if target_fact_id in self._revealed_fact_ids:
            return EnvironmentResponse(
                action=action,
                target_fact_id=target_fact_id,
                status=EnvironmentStatus.ALREADY_REVEALED,
            )

        evidence = self._answers.get((action, target_fact_id))
        if evidence is None:
            return EnvironmentResponse(
                action=action,
                target_fact_id=target_fact_id,
                status=EnvironmentStatus.NOT_AVAILABLE,
            )

        self._revealed_fact_ids.add(target_fact_id)
        return EnvironmentResponse(
            action=action,
            target_fact_id=target_fact_id,
            status=EnvironmentStatus.REVEALED,
            new_facts=[evidence],
        )
