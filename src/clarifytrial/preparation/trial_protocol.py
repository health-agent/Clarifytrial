"""Convert candidate trial text into cited criteria and information requests."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from ..agents.base import StructuredAgent
from ..contracts import NextAction, NextEvidenceRequest, TrialCriterion
from ..interactive.burden_contracts import AcquisitionOption
from ..trace import TraceRecorder
from ..workflow import ScreeningTrial
from .contracts import (
    AcquisitionPathInput,
    TrialProtocolDraft,
    TrialProtocolSource,
)


class TrialProtocolStructurerAgent(StructuredAgent[TrialProtocolDraft]):
    """Extract criteria only from exact quoted spans in a supplied protocol."""

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


def structure_trial_protocol(
    source: TrialProtocolSource,
    agent: TrialProtocolStructurerAgent,
    *,
    known_needs: dict[str, DeclaredInformationNeed] | None = None,
    trace: TraceRecorder,
) -> PreparedTrial:
    """Verify source spans and assign criterion IDs outside the language model."""

    draft = agent.run(
        {
            "trial_id": source.trial_id,
            "title": source.title,
            "conditions": source.conditions,
            "summary": source.summary,
            "source_location": source.source_location,
            "eligibility_text": source.eligibility_text,
            "known_information_needs": [
                {
                    "fact_key": item.fact_key,
                    "description": item.description,
                    "acceptable_actions": [
                        action.value for action in item.acceptable_actions
                    ],
                }
                for item in (known_needs or {}).values()
            ],
        },
        trace=trace,
        cycle=0,
        input_refs=[source.trial_id, source.source_location],
    ).output
    criteria = []
    needs: list[PreparedInformationNeed] = []
    for index, item in enumerate(draft.criteria, start=1):
        if item.end_char > len(source.eligibility_text):
            raise ValueError("criterion quote ends outside the supplied protocol")
        if source.eligibility_text[item.start_char : item.end_char] != item.source_quote:
            raise ValueError(
                "criterion quote and character offsets do not match the supplied protocol"
            )
        criterion_id = f"{source.trial_id}:{item.kind.value}:{index:03d}"
        criteria.append(
            TrialCriterion(
                criterion_id=criterion_id,
                trial_id=source.trial_id,
                kind=item.kind,
                statement=item.statement,
                source_location=(
                    f"{source.source_location}#chars={item.start_char}-{item.end_char}"
                ),
                required=item.required,
                numeric_constraint=item.numeric_constraint,
                evidence_requirement=item.evidence_requirement,
            )
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
    trace.record(
        cycle=0,
        actor="trial_protocol_quote_checks",
        event="trial_protocol_structured",
        input_refs=[source.trial_id],
        output={
            "criterion_ids": [item.criterion_id for item in criteria],
            "information_need_count": len(needs),
        },
    )
    return PreparedTrial(
        trial=ScreeningTrial(trial_id=source.trial_id, criteria=criteria),
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
            if row["description"] != need.description:
                raise ValueError(
                    f"fact_key {need.fact_key!r} has conflicting descriptions"
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
