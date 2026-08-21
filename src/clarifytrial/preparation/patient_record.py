"""Convert a natural-language patient record into cited workflow facts."""

from __future__ import annotations

from ..agents.base import StructuredAgent
from ..contracts import EvidenceFact, PatientState
from ..trace import TraceRecorder
from .contracts import PatientRecordDraft, RawPatientRecord


class PatientRecordStructurerAgent(StructuredAgent[PatientRecordDraft]):
    """Extract only facts that point to an exact part of the supplied record."""

    agent_name = "patient_record_structurer"
    prompt_id = "prompts/patient_record_structurer.md"
    response_model = PatientRecordDraft


def _verify_quote(text: str, quote: str, start_char: int, end_char: int) -> None:
    if end_char > len(text):
        raise ValueError("patient fact quote ends outside the supplied record")
    if text[start_char:end_char] != quote:
        raise ValueError(
            "patient fact quote and character offsets do not match the supplied record"
        )


def structure_patient_record(
    record: RawPatientRecord,
    agent: PatientRecordStructurerAgent,
    *,
    trace: TraceRecorder,
) -> tuple[PatientState, list[str]]:
    """Call the structurer, verify every quote, and assign stable evidence IDs."""

    draft = agent.run(
        {
            "patient_id": record.patient_id,
            "source_id": record.source_id,
            "recorded_at": record.recorded_at.isoformat(),
            "as_of": record.as_of.isoformat(),
            "source_type": record.source_type.value,
            "verification_status": record.verification_status.value,
            "record_text": record.text,
        },
        trace=trace,
        cycle=0,
        input_refs=[record.patient_id, record.source_id],
    ).output
    search_conditions = []
    for item in draft.search_conditions:
        _verify_quote(
            record.text,
            item.source_quote,
            item.start_char,
            item.end_char,
        )
        search_conditions.append(item.condition)
    facts = []
    for index, item in enumerate(draft.facts, start=1):
        _verify_quote(
            record.text,
            item.source_quote,
            item.start_char,
            item.end_char,
        )
        facts.append(
            EvidenceFact(
                evidence_id=f"patient:{record.source_id}:{index:03d}",
                statement=item.statement,
                source_type=record.source_type,
                source_location=(
                    f"{record.source_id}#chars={item.start_char}-{item.end_char}"
                ),
                event_date=item.event_date,
                recorded_date=record.recorded_at.date(),
                verification_status=record.verification_status,
                concept=item.concept,
                value=item.value,
                unit=item.unit,
            )
        )
    trace.record(
        cycle=0,
        actor="patient_record_quote_checks",
        event="patient_record_structured",
        input_refs=[record.source_id],
        output={
            "search_conditions": search_conditions,
            "evidence_ids": [item.evidence_id for item in facts],
        },
    )
    return (
        PatientState(patient_id=record.patient_id, as_of=record.as_of, facts=facts),
        search_conditions,
    )
