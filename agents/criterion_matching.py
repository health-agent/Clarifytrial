"""Criterion Matching & Reasoning Agent.

Responsibility: judge each criterion against the patient profile (and
evidence context) and emit a ``criterion_match_status`` (met / unmet /
unknown / conflict / not_applicable). The mapping from status to
eligibility effect is NOT done here; it is the pure rule
``rules.derive_eligibility_effect``, which this module calls — never
reimplements.

Current matching implementation: a deterministic, conservative offline
heuristic. Core principle: missing information is NEVER negative
evidence — if the profile lacks a required variable, the status is
``unknown`` and a canonical ``missing_variable_key`` is emitted so the
clarification loop can ask for it. LLM structured matching, driven by
``prompts/criterion_matching.md``, will replace the fallback behind the
same typed contract.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from models import (
    Criterion,
    CriterionMatchStatus,
    CriterionState,
    EvidenceContext,
    PatientProfile,
    SourceSentence,
)
from rules import derive_eligibility_effect

_AGE_MIN_RE = re.compile(r"age\s+(\d{1,3})\s+years?\s+or\s+older", re.IGNORECASE)
_AGE_RANGE_RE = re.compile(r"age\s+(\d{1,3})\s*-\s*(\d{1,3})\s+years?", re.IGNORECASE)

_TRUE_VALUES = {True, "true", "yes", "present", "positive"}
_FALSE_VALUES = {False, "false", "no", "none", "absent", "negative"}


def _lookup_variable(profile: PatientProfile, variable: str) -> Any:
    """Fetch a canonical variable's value from the profile, or None.

    Accepts both canonical keys and the aliases the current profile
    extractor produces (e.g. 'prior_treatments', 'biomarkers', 'stage').
    """
    aliases = {
        "age": [],
        "sex": [],
        "disease_stage": ["stage"],
        "biomarker_status": ["biomarkers"],
        "prior_treatment": ["prior_treatments"],
        "current_treatment": ["current_treatments"],
        "renal_function": ["egfr", "creatinine_clearance"],
    }
    if variable == "age":
        return profile.age
    if variable == "sex":
        return profile.sex
    for key in [variable] + aliases.get(variable, []):
        if key in profile.variables:
            return profile.variables[key]
    return None


def _is_known(value: Any) -> bool:
    return value is not None and value != "unknown"


def match_criterion_against_patient(
    patient_profile: PatientProfile, criterion: Criterion
) -> CriterionState:
    """Match one criterion against one patient profile.

    Deterministic conservative fallback (no LLM, no network, no keys):

    - value recorded as "conflict" for a required variable -> ``conflict``;
    - explicit age threshold/range in the text + known age -> met/unmet;
    - required diagnosis known and clearly appearing in the criterion
      text -> ``met`` (for exclusions this means the patient HAS the
      excluded condition — the effect inversion is the rules' job);
    - a single known boolean-like variable -> met (truthy) / unmet (falsy);
    - any required variable missing from the profile -> ``unknown`` with
      canonical missing_variable_keys (never negative evidence);
    - anything else it cannot safely judge -> ``unknown``.

    The eligibility effect and review flags always come from
    ``rules.derive_eligibility_effect`` — never computed locally.

    TODO: replace the heuristics with LLM structured matching using
    prompts/criterion_matching.md (evidence summaries, confidence,
    conflict detection across sources), keeping this exact signature.
    """
    required = criterion.required_variables
    values = {v: _lookup_variable(patient_profile, v) for v in required}
    missing = [v for v in required if not _is_known(values[v])]
    known = {v: val for v, val in values.items() if _is_known(val)}

    status: CriterionMatchStatus
    evidence_bits: list[str] = []
    fields_used: list[str] = []
    status_decided = False

    # 1) Explicitly recorded contradictions -> conflict.
    if any(val == "conflict" for val in known.values()):
        status = CriterionMatchStatus.conflict
        conflicted = [v for v, val in known.items() if val == "conflict"]
        evidence_bits.append(f"conflicting recorded values for: {', '.join(conflicted)}")
        fields_used.extend(conflicted)
        status_decided = True

    # 2) Age threshold/range explicitly in the text with a known age.
    if not status_decided and "age" in known:
        age = int(known["age"])
        min_match = _AGE_MIN_RE.search(criterion.text)
        range_match = _AGE_RANGE_RE.search(criterion.text)
        if min_match:
            ok = age >= int(min_match.group(1))
            status = CriterionMatchStatus.met if ok else CriterionMatchStatus.unmet
            evidence_bits.append(f"age={age} vs threshold {min_match.group(0)!r}")
            fields_used.append("age")
            status_decided = True
        elif range_match:
            low, high = int(range_match.group(1)), int(range_match.group(2))
            ok = low <= age <= high
            status = CriterionMatchStatus.met if ok else CriterionMatchStatus.unmet
            evidence_bits.append(f"age={age} vs range {range_match.group(0)!r}")
            fields_used.append("age")
            status_decided = True

    # 3) Diagnosis clearly matching the criterion text.
    if (
        not status_decided
        and "diagnosis" in known
        and isinstance(known["diagnosis"], str)
        and known["diagnosis"].lower() in criterion.text.lower()
    ):
        status = CriterionMatchStatus.met
        evidence_bits.append(f"diagnosis '{known['diagnosis']}' stated in criterion text")
        fields_used.append("variables.diagnosis")
        status_decided = True

    # 4) A single known boolean-like variable decides directly.
    if not status_decided and len(required) == 1 and required[0] in known:
        val = known[required[0]]
        normalized = val.lower() if isinstance(val, str) else val
        if normalized in _TRUE_VALUES:
            status = CriterionMatchStatus.met
            evidence_bits.append(f"{required[0]}={val!r}")
            fields_used.append(f"variables.{required[0]}")
            status_decided = True
        elif normalized in _FALSE_VALUES:
            status = CriterionMatchStatus.unmet
            evidence_bits.append(f"{required[0]}={val!r}")
            fields_used.append(f"variables.{required[0]}")
            status_decided = True

    # 5) Missing information -> unknown with missing keys (never 'unmet').
    if not status_decided:
        status = CriterionMatchStatus.unknown
        if missing:
            evidence_bits.append(f"required variables not in profile: {', '.join(missing)}")

    effect, review_required, review_reason = derive_eligibility_effect(
        criterion.criterion_type, status
    )

    evidence = EvidenceContext(
        criterion_id=criterion.criterion_id,
        sentences=[
            SourceSentence(
                sentence_id=f"{criterion.criterion_id}-ev-{i + 1}",
                text=bit,
                source="patient_profile",
            )
            for i, bit in enumerate(evidence_bits)
        ],
        profile_fields_used=fields_used,
    )

    return CriterionState(
        criterion_id=criterion.criterion_id,
        trial_id=criterion.trial_id,
        criterion_type=criterion.criterion_type,
        criterion_match_status=status,
        eligibility_effect=effect,
        review_required=review_required,
        review_reason=review_reason,
        missing_variable_keys=missing if status == CriterionMatchStatus.unknown else [],
        evidence=evidence,
    )


def match_criterion(
    criterion: Criterion, evidence: EvidenceContext
) -> CriterionMatchStatus:
    """Judge one criterion from its evidence.

    TODO: LLM call comparing evidence against the criterion text; return
    conflict when sources disagree, unknown when required variables are
    absent, not_applicable when the criterion does not apply.
    """
    raise NotImplementedError("LLM-based matching not implemented in skeleton")
