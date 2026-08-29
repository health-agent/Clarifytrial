"""Convert candidate trial text into cited criteria and information requests."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from ..agents.base import StructuredAgent
from ..contracts import NextAction, NextEvidenceRequest, TrialCriterion
from ..interactive.burden_contracts import AcquisitionOption
from ..trace import TraceRecorder
from ..workflow import ScreeningTrial
from .contracts import (
    AcquisitionPathInput,
    TrialCriterionDraft,
    TrialProtocolDraft,
    TrialProtocolSource,
)
from .source_validation import (
    SourceSpan,
    SourceValidationError,
    comparison_text,
    remove_unsupported_evidence_requirements,
    remove_unwritten_equality_constraint,
    resolve_source_span,
    validate_trial_criterion_source,
)


DEFAULT_PROTOCOL_CHUNK_CHAR_LIMIT = 8_000

_SECTION_HEADER = re.compile(
    r"(?im)^[ \t]*(inclusion|exclusion)[ \t]+criteria[ \t]*:?[ \t]*$"
)
_COMPLEX_LOGIC_PATTERNS = (
    (
        re.compile(
            r"(?i)\b(?:either|one|any|at\s+least\s+(?:one|two|three|\d+))"
            r"\b[^\n:]{0,60}\bof\s+the\s+following\b"
        ),
        "원문에 여러 조건 중 일부를 고르는 경로가 있지만 현재 구조에는 그 경로가 표현되지 않았다.",
    ),
    (
        re.compile(r"(?i)\b(?:unless|except(?:ion)?(?:\s+to)?)\b"),
        "원문에 예외 조건이 있지만 현재 구조에는 그 예외가 표현되지 않았다.",
    ),
    (
        re.compile(r"(?i)\bwhichever\s+is\s+(?:longer|shorter)\b"),
        "원문에 상황에 따라 달라지는 기간 규칙이 있지만 현재 구조에는 그 규칙이 표현되지 않았다.",
    ),
)


def _section_kind_at(text: str, position: int) -> str | None:
    """Return the nearest explicit inclusion or exclusion section."""

    matches = list(_SECTION_HEADER.finditer(text, 0, position))
    if not matches:
        return None
    return matches[-1].group(1).casefold()


def _section_start_hint(
    full_text: str,
    chunk: _EligibilityChunk,
    kind: str,
) -> int | None:
    local_matches = [
        item
        for item in _SECTION_HEADER.finditer(chunk.text)
        if item.group(1).casefold() == kind
    ]
    if local_matches:
        return local_matches[-1].end()
    if _section_kind_at(full_text, chunk.start_char) == kind:
        return 0
    return None


def _unsupported_logic_issues(text: str) -> list[str]:
    return [message for pattern, message in _COMPLEX_LOGIC_PATTERNS if pattern.search(text)]


def _resolve_criterion_source_span(
    *,
    source_text: str,
    source_quote: str,
    approximate_start_char: int | None,
    approximate_end_char: int | None,
    occurrence_index: int | None,
    context_hint: str,
) -> tuple[SourceSpan, str | None]:
    """Resolve one real source span even when a model joins separate list lines."""

    try:
        return (
            resolve_source_span(
                source_text,
                source_quote,
                approximate_start_char=approximate_start_char,
                approximate_end_char=approximate_end_char,
                occurrence_index=occurrence_index,
                context_hint=context_hint,
            ),
            None,
        )
    except SourceValidationError as error:
        if str(error) != "quoted source text was not found":
            raise

    lines = []
    for raw_line in source_quote.splitlines():
        line = re.sub(r"^\s*(?:[-*+]|\d+[.)])\s*", "", raw_line).strip()
        if len(comparison_text(line)) >= 12:
            lines.append(line)
    if len(lines) < 2:
        raise SourceValidationError("quoted source text was not found")

    for line in reversed(lines):
        try:
            match = resolve_source_span(
                source_text,
                line,
                approximate_start_char=approximate_start_char,
                context_hint=context_hint,
            )
        except SourceValidationError:
            continue
        return (
            SourceSpan(
                start_char=match.start_char,
                end_char=match.end_char,
                source_text=match.source_text,
                match_method=f"composite_quote_line:{match.match_method}",
            ),
            (
                "모델이 서로 떨어진 원문 구절을 한 인용문으로 이었기 때문에 "
                "실제 조건이 적힌 연속 문장만 근거로 사용했다."
            ),
        )
    raise SourceValidationError("quoted source text was not found")


def _remove_unverified_structured_fields(
    item: TrialCriterionDraft,
    source_text: str,
) -> tuple[TrialCriterionDraft, list[str], list[str]]:
    """Drop only model-added calculation fields that the cited text cannot prove."""

    issues: list[str] = []
    corrections: list[str] = []
    numeric_valid = True
    if item.numeric_constraint is not None:
        try:
            validate_trial_criterion_source(
                item.model_copy(update={"evidence_requirement": None}),
                source_text,
            )
        except SourceValidationError as error:
            numeric_valid = False
            corrections.append(f"numeric_constraint_removed: {error}")
            issues.append(
                "원문에서 자동 계산용 수치 조건을 확인하지 못해 문장 판단으로 남겼다."
            )

    evidence_valid = True
    if item.evidence_requirement is not None:
        try:
            validate_trial_criterion_source(
                item.model_copy(update={"numeric_constraint": None}),
                source_text,
            )
        except SourceValidationError as error:
            evidence_valid = False
            corrections.append(f"evidence_requirement_removed: {error}")
            issues.append(
                "원문에서 자동 확인용 자료 요건을 확인하지 못해 문장 판단으로 남겼다."
            )

    sanitized = item.model_copy(
        update={
            "numeric_constraint": (
                item.numeric_constraint if numeric_valid else None
            ),
            "evidence_requirement": (
                item.evidence_requirement if evidence_valid else None
            ),
        }
    )
    validate_trial_criterion_source(sanitized, source_text)
    return sanitized, corrections, issues


class TrialProtocolStructurerAgent(StructuredAgent[TrialProtocolDraft]):
    """Extract criteria only from grounded parts of a supplied protocol."""

    agent_name = "trial_protocol_structurer"
    prompt_id = "prompts/trial_protocol_structurer.md"
    response_model = TrialProtocolDraft


@dataclass(frozen=True, slots=True)
class PreparedInformationNeed:
    """One missing fact linked to the criterion that requires it."""

    fact_key: str
    description: str
    acceptable_actions: tuple[NextAction, ...]
    criterion_id: str


@dataclass(frozen=True, slots=True)
class PreparedTrial:
    trial: ScreeningTrial
    needs: tuple[PreparedInformationNeed, ...]


@dataclass(frozen=True, slots=True)
class DeclaredInformationNeed:
    """Canonical wording and permitted routes supplied outside the model."""

    fact_key: str
    description: str
    acceptable_actions: tuple[NextAction, ...]


def declared_information_needs(
    paths: list[AcquisitionPathInput],
) -> dict[str, DeclaredInformationNeed]:
    """Build one stable missing-fact definition from declared acquisition paths."""

    grouped: dict[str, dict] = {}
    for path in paths:
        row = grouped.setdefault(
            path.fact_key,
            {"description": path.fact_description, "actions": []},
        )
        if row["description"] != path.fact_description:
            raise ValueError(
                f"fact_key {path.fact_key!r} has conflicting descriptions"
            )
        if path.action not in row["actions"]:
            row["actions"].append(path.action)
    return {
        fact_key: DeclaredInformationNeed(
            fact_key=fact_key,
            description=row["description"],
            acceptable_actions=tuple(row["actions"]),
        )
        for fact_key, row in sorted(grouped.items())
    }


def _fact_id(fact_key: str) -> str:
    digest = hashlib.sha256(fact_key.casefold().encode("utf-8")).hexdigest()[:12]
    return f"missing:{digest}"


@dataclass(frozen=True, slots=True)
class _EligibilityChunk:
    text: str
    start_char: int


def _eligibility_chunks(
    text: str,
    *,
    char_limit: int,
) -> tuple[_EligibilityChunk, ...]:
    """Split long protocols only at line boundaries so criteria stay intact."""

    if char_limit < 1:
        raise ValueError("char_limit must be positive")
    if len(text) <= char_limit:
        return (_EligibilityChunk(text=text, start_char=0),)
    chunks: list[_EligibilityChunk] = []
    current: list[str] = []
    current_length = 0
    current_start = 0
    consumed = 0
    for line in text.splitlines(keepends=True):
        if current and current_length + len(line) > char_limit:
            chunks.append(
                _EligibilityChunk(
                    text="".join(current),
                    start_char=current_start,
                )
            )
            current = []
            current_length = 0
            current_start = consumed
        current.append(line)
        current_length += len(line)
        consumed += len(line)
    if current:
        chunks.append(
            _EligibilityChunk(
                text="".join(current),
                start_char=current_start,
            )
        )
    return tuple(item for item in chunks if item.text.strip())


def structure_trial_protocol(
    source: TrialProtocolSource,
    agent: TrialProtocolStructurerAgent,
    *,
    known_needs: dict[str, DeclaredInformationNeed] | None = None,
    chunk_char_limit: int = DEFAULT_PROTOCOL_CHUNK_CHAR_LIMIT,
    trace: TraceRecorder,
) -> PreparedTrial:
    """Ground criteria, check decision fields, and assign criterion IDs in code."""

    chunks = _eligibility_chunks(
        source.eligibility_text,
        char_limit=chunk_char_limit,
    )
    draft_items = []
    known_information_needs = [
        {
            "fact_key": item.fact_key,
            "description": item.description,
            "acceptable_actions": [
                action.value for action in item.acceptable_actions
            ],
        }
        for item in (known_needs or {}).values()
    ]
    for chunk_index, chunk in enumerate(chunks, start=1):
        draft = agent.run(
            {
            "trial_id": source.trial_id,
            "title": source.title,
            "conditions": source.conditions,
            "summary": source.summary,
            "source_location": source.source_location,
                "eligibility_text": chunk.text,
                "known_information_needs": known_information_needs,
                "chunk": {
                    "index": chunk_index,
                    "count": len(chunks),
                },
            },
            trace=trace,
            cycle=0,
            input_refs=[
                source.trial_id,
                source.source_location,
                f"chunk:{chunk_index}/{len(chunks)}",
            ],
        ).output
        draft_items.extend((item, chunk) for item in draft.criteria)
    criteria = []
    needs: list[PreparedInformationNeed] = []
    source_matches = []
    kind_corrections = []
    protocol_logic_issues = _unsupported_logic_issues(source.eligibility_text)
    draft_quote_counts: dict[tuple[int, str], int] = {}
    for draft_item, draft_chunk in draft_items:
        key = (draft_chunk.start_char, comparison_text(draft_item.source_quote))
        draft_quote_counts[key] = draft_quote_counts.get(key, 0) + 1
    used_quote_occurrences: dict[tuple[int, str], int] = {}
    for index, (item, chunk) in enumerate(draft_items, start=1):
        quote_key = (chunk.start_char, comparison_text(item.source_quote))
        occurrence_index = None
        if draft_quote_counts[quote_key] > 1:
            occurrence_index = used_quote_occurrences.get(quote_key, 0)
            used_quote_occurrences[quote_key] = occurrence_index + 1
        start_hint = item.start_char
        if start_hint is None:
            start_hint = _section_start_hint(
                source.eligibility_text,
                chunk,
                item.kind.value,
            )
        context_hint = " ".join(
            value
            for value in (
                item.statement,
                (
                    None
                    if item.numeric_constraint is None
                    else item.numeric_constraint.concept
                ),
            )
            if value
        )
        match, quote_issue = _resolve_criterion_source_span(
            source_text=chunk.text,
            source_quote=item.source_quote,
            approximate_start_char=start_hint,
            approximate_end_char=item.end_char,
            occurrence_index=occurrence_index,
            context_hint=context_hint,
        )
        if quote_issue is not None:
            protocol_logic_issues.append(quote_issue)
        item, evidence_requirement_corrections = (
            remove_unsupported_evidence_requirements(
                item,
                match.source_text,
                allow_remove_recency=bool(protocol_logic_issues),
            )
        )
        item, numeric_constraint_corrections = (
            remove_unwritten_equality_constraint(item, match.source_text)
        )
        item, structured_field_corrections, structured_field_issues = (
            _remove_unverified_structured_fields(item, match.source_text)
        )
        protocol_logic_issues.extend(structured_field_issues)
        global_start = chunk.start_char + match.start_char
        global_end = chunk.start_char + match.end_char
        explicit_section = _section_kind_at(source.eligibility_text, global_start)
        resolved_kind = item.kind
        if explicit_section is not None and explicit_section != item.kind.value:
            resolved_kind = type(item.kind)(explicit_section)
            kind_corrections.append(
                {
                    "criterion_index": index,
                    "model_kind": item.kind.value,
                    "source_section": explicit_section,
                }
            )
        criterion_id = f"{source.trial_id}:{resolved_kind.value}:{index:03d}"
        criteria.append(
            TrialCriterion(
                criterion_id=criterion_id,
                trial_id=source.trial_id,
                kind=resolved_kind,
                statement=match.source_text.strip(),
                source_location=(
                    f"{source.source_location}#chars={global_start}-{global_end}"
                ),
                # Every eligibility item extracted from prose is a condition to
                # evaluate. Optional grouping is only accepted in manually
                # authored structured inputs, where the grouping is explicit.
                required=True,
                numeric_constraint=item.numeric_constraint,
                evidence_requirement=item.evidence_requirement,
            )
        )
        source_matches.append(
            {
                "criterion_id": criterion_id,
                "start_char": global_start,
                "end_char": global_end,
                "match_method": match.match_method,
                "evidence_requirement_corrections": (
                    evidence_requirement_corrections
                ),
                "numeric_constraint_corrections": (
                    numeric_constraint_corrections
                ),
                "structured_field_corrections": structured_field_corrections,
            }
        )
        for need in item.information_needs:
            declared = (known_needs or {}).get(need.fact_key)
            needs.append(
                PreparedInformationNeed(
                    fact_key=need.fact_key,
                    description=(
                        declared.description if declared else need.description
                    ),
                    acceptable_actions=(
                        declared.acceptable_actions
                        if declared
                        else tuple(need.acceptable_actions)
                    ),
                    criterion_id=criterion_id,
                )
            )
    protocol_logic_issues = list(dict.fromkeys(protocol_logic_issues))
    trace.record(
        cycle=0,
        actor="trial_protocol_source_checks",
        event="trial_protocol_structured",
        input_refs=[source.trial_id],
        output={
            "criterion_ids": [item.criterion_id for item in criteria],
            "protocol_chunk_count": len(chunks),
            "information_need_count": len(needs),
            "source_matches": source_matches,
            "kind_corrections": kind_corrections,
            "protocol_logic_supported": not protocol_logic_issues,
            "protocol_logic_issues": protocol_logic_issues,
        },
    )
    return PreparedTrial(
        trial=ScreeningTrial(
            trial_id=source.trial_id,
            criteria=criteria,
            protocol_logic_supported=not protocol_logic_issues,
            protocol_logic_issues=protocol_logic_issues,
        ),
        needs=tuple(needs),
    )


def merge_information_requests(
    prepared_trials: list[PreparedTrial],
) -> tuple[list[NextEvidenceRequest], dict[str, str]]:
    """Merge the same fact key across trials without losing criterion links."""

    grouped: dict[str, dict] = {}
    for prepared in prepared_trials:
        for need in prepared.needs:
            row = grouped.setdefault(
                need.fact_key,
                {
                    "description": need.description,
                    "actions": [],
                    "criterion_ids": [],
                },
            )
            for action in need.acceptable_actions:
                if action not in row["actions"]:
                    row["actions"].append(action)
            if need.criterion_id not in row["criterion_ids"]:
                row["criterion_ids"].append(need.criterion_id)

    fact_id_by_key = {fact_key: _fact_id(fact_key) for fact_key in sorted(grouped)}
    requests = [
        NextEvidenceRequest(
            fact_id=fact_id_by_key[fact_key],
            description=row["description"],
            related_criterion_ids=sorted(row["criterion_ids"]),
            acceptable_actions=row["actions"],
            reason=(
                "이 사실은 현재 자료만으로 확인하기 어려운 조건에 연결되어 있다."
            ),
        )
        for fact_key, row in sorted(grouped.items())
    ]
    return requests, fact_id_by_key


def build_acquisition_options(
    paths: list[AcquisitionPathInput],
    *,
    fact_id_by_key: dict[str, str],
    requests: list[NextEvidenceRequest],
) -> list[AcquisitionOption]:
    """Attach declared availability and burden without asking the model to guess."""

    request_by_id = {item.fact_id: item for item in requests}
    options = []
    seen_keys: set[tuple[str, str]] = set()
    for path in paths:
        key = (path.fact_key, path.path_key)
        if key in seen_keys:
            raise ValueError("acquisition paths must not repeat fact_key and path_key")
        seen_keys.add(key)
        fact_id = fact_id_by_key.get(path.fact_key)
        if fact_id is None:
            raise ValueError(
                f"acquisition path refers to unused fact_key: {path.fact_key}"
            )
        if path.action not in request_by_id[fact_id].acceptable_actions:
            raise ValueError(
                f"acquisition path {path.path_key!r} is not allowed for {path.fact_key}"
            )
        options.append(
            AcquisitionOption(
                option_id=f"{fact_id}:{path.path_key}",
                fact_id=fact_id,
                action=path.action,
                acquisition_mode=path.acquisition_mode,
                available_now=path.available_now,
                expected_delay_hours=path.expected_delay_hours,
                visit_required=path.visit_required,
                direct_cost_band=path.direct_cost_band,
                physical_burden_0_to_3=path.physical_burden_0_to_3,
                emotional_burden_0_to_3=path.emotional_burden_0_to_3,
                medical_risk_0_to_3=path.medical_risk_0_to_3,
                treatment_disruption_0_to_3=path.treatment_disruption_0_to_3,
                already_planned_in_care=path.already_planned_in_care,
                new_test_required=path.new_test_required,
                requires_patient_choice=path.requires_patient_choice,
                requires_clinician_authorization=(
                    path.requires_clinician_authorization
                ),
                source_note=path.source_note,
            )
        )
    return options
