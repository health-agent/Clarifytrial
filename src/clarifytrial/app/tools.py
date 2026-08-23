"""Interactive answers and resumable session storage for structured runs."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from ..contracts import (
    AgentAction,
    EvidenceFact,
    EvidenceSourceType,
    NextAction,
    PatientState,
    VerificationStatus,
)
from ..environment import EnvironmentStatus, ToolExecutionResult
from .contracts import ScreeningSession, SessionEvent


_SOURCE_BY_ACTION = {
    NextAction.ASK_PATIENT: EvidenceSourceType.PATIENT_REPORT,
    NextAction.LOOKUP_RECORD: EvidenceSourceType.MEDICAL_RECORD,
    NextAction.REQUEST_VERIFICATION: EvidenceSourceType.OFFICIAL_VERIFICATION,
}

_VERIFICATION_BY_ACTION = {
    NextAction.ASK_PATIENT: VerificationStatus.REPORTED,
    NextAction.LOOKUP_RECORD: VerificationStatus.VERIFIED,
    NextAction.REQUEST_VERIFICATION: VerificationStatus.VERIFIED,
}


class InteractiveSessionPaused(RuntimeError):
    """Raised after the user explicitly saves and leaves an unfinished run."""


class SessionStore:
    def __init__(self, path: str | Path, session: ScreeningSession) -> None:
        self.path = Path(path)
        self.session = session

    @classmethod
    def load(cls, path: str | Path) -> "SessionStore":
        source = Path(path)
        return cls(
            source,
            ScreeningSession.model_validate_json(source.read_text(encoding="utf-8")),
        )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            self.session.model_dump_json(indent=2) + "\n",
            encoding="utf-8",
        )

    def record(
        self,
        *,
        fact_id: str,
        action: NextAction,
        status: EnvironmentStatus,
        patient_state: PatientState,
        evidence: EvidenceFact | None,
    ) -> None:
        revealed = list(self.session.revealed_fact_ids)
        if status is EnvironmentStatus.REVEALED and fact_id not in revealed:
            revealed.append(fact_id)
        event = SessionEvent(
            step=self.session.action_count + 1,
            fact_id=fact_id,
            action=action.value,
            status=status.value,
            evidence_id=None if evidence is None else evidence.evidence_id,
        )
        self.session = self.session.model_copy(
            update={
                "patient_state": patient_state,
                "revealed_fact_ids": revealed,
                "action_count": self.session.action_count + 1,
                "events": [*self.session.events, event],
            }
        )
        self.save()


def _read_user_payload(raw: str) -> Mapping[str, Any]:
    if raw.startswith("@"):
        value = json.loads(Path(raw[1:].strip()).read_text(encoding="utf-8"))
    elif raw.startswith("{"):
        value = json.loads(raw)
    else:
        value = {"statement": raw}
    if not isinstance(value, Mapping):
        raise ValueError("answer must be text, one JSON object, or @path-to-json")
    return value


def evidence_from_user_input(
    *,
    raw: str,
    action: NextAction,
    fact_id: str,
    patient_state: PatientState,
    step: int,
) -> EvidenceFact:
    payload = dict(_read_user_payload(raw))
    statement = str(payload.pop("statement", "")).strip()
    if not statement:
        raise ValueError("answer needs a non-empty statement")
    as_of = patient_state.as_of.date()
    safe_fact_id = "".join(
        char if char.isalnum() or char in "-_" else "-" for char in fact_id
    )
    base = {
        "evidence_id": f"interactive-{safe_fact_id}-{step}",
        "statement": statement,
        "source_type": _SOURCE_BY_ACTION[action],
        "source_location": f"interactive:{action.value}:{fact_id}",
        "event_date": payload.pop("event_date", as_of),
        "recorded_date": payload.pop("recorded_date", as_of),
        "verification_status": _VERIFICATION_BY_ACTION[action],
        "concept": payload.pop("concept", None),
        "value": payload.pop("value", None),
        "unit": payload.pop("unit", None),
    }
    if payload:
        raise ValueError(
            "unknown answer fields: " + ", ".join(sorted(str(item) for item in payload))
        )
    return EvidenceFact.model_validate(base)


class InteractiveInformationTools:
    """Ask for one observable answer and save the resulting patient state."""

    def __init__(
        self,
        store: SessionStore,
        *,
        read: Callable[[str], str] = input,
        write: Callable[[str], None] = print,
    ) -> None:
        self.store = store
        self.read = read
        self.write = write

    def execute(
        self,
        agent_action: AgentAction,
        patient_state: PatientState,
    ) -> ToolExecutionResult:
        fact_id = agent_action.target_fact_id
        if fact_id is None or agent_action.action not in _SOURCE_BY_ACTION:
            return ToolExecutionResult(
                action=agent_action.action,
                target_fact_id=fact_id,
                status=EnvironmentStatus.NOT_AVAILABLE,
                patient_state=patient_state,
            )
        self.write("")
        self.write(f"확인할 정보: {agent_action.message or agent_action.reason}")
        self.write("답변 문장, JSON 객체, 또는 @JSON파일 경로를 입력하세요.")
        self.write("값을 알 수 없으면 unknown, 저장하고 나가려면 quit을 입력하세요.")
        while True:
            raw = self.read("답변: ").strip()
            if raw.casefold() in {"quit", "exit", "save"}:
                self.store.save()
                raise InteractiveSessionPaused("interactive session saved")
            if raw.casefold() in {"unknown", "모름", "?", "skip"}:
                result = ToolExecutionResult(
                    action=agent_action.action,
                    target_fact_id=fact_id,
                    status=EnvironmentStatus.NOT_AVAILABLE,
                    patient_state=patient_state,
                )
                self.store.record(
                    fact_id=fact_id,
                    action=agent_action.action,
                    status=result.status,
                    patient_state=patient_state,
                    evidence=None,
                )
                return result
            try:
                evidence = evidence_from_user_input(
                    raw=raw,
                    action=agent_action.action,
                    fact_id=fact_id,
                    patient_state=patient_state,
                    step=self.store.session.action_count + 1,
                )
            except (ValueError, OSError, json.JSONDecodeError) as error:
                self.write(f"입력을 읽지 못했습니다: {error}")
                continue
            updated = patient_state.model_copy(
                update={"facts": [*patient_state.facts, evidence]}
            )
            result = ToolExecutionResult(
                action=agent_action.action,
                target_fact_id=fact_id,
                status=EnvironmentStatus.REVEALED,
                new_facts=[evidence],
                patient_state=updated,
            )
            self.store.record(
                fact_id=fact_id,
                action=agent_action.action,
                status=result.status,
                patient_state=updated,
                evidence=evidence,
            )
            return result


__all__ = [
    "InteractiveInformationTools",
    "InteractiveSessionPaused",
    "SessionStore",
    "evidence_from_user_input",
]
