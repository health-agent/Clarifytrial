"""Convert a natural-language patient record into cited workflow facts."""

from __future__ import annotations

from ..agents.base import StructuredAgent
from ..contracts import EvidenceFact, PatientState
from ..trace import TraceRecorder
from .contracts import PatientRecordDraft, RawPatientRecord
from .source_validation import (
    resolve_source_span,
    validate_patient_fact_source,
)


class PatientRecordStructurerAgent(StructuredAgent[PatientRecordDraft]):
    """Extract only facts that can be grounded in the supplied record."""

    agent_name = "patient_record_structurer"
    prompt_id = "prompts/patient_record_structurer.md"
    response_model = PatientRecordDraft


def structure_patient_record(
    record: RawPatientRecord,
    agent: PatientRecordStructurerAgent,
    *,
    trace: TraceRecorder,
) -> tuple[PatientState, list[str]]:
    """Ground proposed facts, check key values, and assign stable evidence IDs."""

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
    source_matches = []
    for item in draft.search_conditions:
        match = resolve_source_span(
            record.text,
            item.source_quote,
            approximate_start_char=item.start_char,
            approximate_end_char=item.end_char,
        )
        search_conditions.append(item.condition)
        source_matches.append(
            {
                "item_type": "search_condition",
                "item_key": item.condition,
                "start_char": match.start_char,
                "end_char": match.end_char,
                "match_method": match.match_method,
            }
        )
    facts = []
    for index, item in enumerate(draft.facts, start=1):
        match = resolve_source_span(
            record.text,
            item.source_quote,
            approximate_start_char=item.start_char,
            approximate_end_char=item.end_char,
        )
        validate_patient_fact_source(item, match.source_text)
        facts.append(
            EvidenceFact(
                evidence_id=f"patient:{record.source_id}:{index:03d}",
                statement=match.source_text.strip(),
                source_type=record.source_type,
                source_location=(
                    f"{record.source_id}#chars={match.start_char}-{match.end_char}"
                ),
                event_date=item.event_date,
                recorded_date=record.recorded_at.date(),
                verification_status=record.verification_status,
                concept=item.concept,
                value=item.value,
                unit=item.unit,
            )
        )
        source_matches.append(
            {
                "item_type": "patient_fact",
                "item_key": item.fact_key,
                "start_char": match.start_char,
                "end_char": match.end_char,
                "match_method": match.match_method,
            }
        )
    trace.record(
        cycle=0,
        actor="patient_record_source_checks",
        event="patient_record_structured",
        input_refs=[record.source_id],
        output={
            "search_conditions": search_conditions,
            "evidence_ids": [item.evidence_id for item in facts],
            "source_matches": source_matches,
        },
    )
    return (
        PatientState(patient_id=record.patient_id, as_of=record.as_of, facts=facts),
        search_conditions,
    )
