"""Criteria Parser Agent.

Responsibility: parse a trial's raw eligibility criteria text
(``TrialProtocol.eligibility_criteria_raw``) into structured
``Criterion`` objects with ``criterion_type`` and ``required_variables``.

Source-agnostic: works on raw text regardless of where the protocol came
from (a future ClinicalTrials.gov API v2 adapter would only populate
``TrialProtocol``; this agent does not know about API response paths).

Current parsing implementation: a deterministic, offline heuristic
fallback (header/line splitting plus keyword variable mapping). No
network calls, no API keys. LLM structured extraction, driven by
``prompts/criteria_parsing.md``, will replace the fallback behind the
same typed contract.

Locked invariant honored here: text OUTSIDE the inclusion/exclusion
sections is treated as trial description (context/relevance only) and
never becomes a criterion.
"""

from __future__ import annotations

import re

from models import Criterion, CriterionType, TrialContext, TrialProtocol

_INCLUSION_HEADER_RE = re.compile(r"^\s*inclusion criteria\s*:?\s*$", re.IGNORECASE)
_EXCLUSION_HEADER_RE = re.compile(r"^\s*exclusion criteria\s*:?\s*$", re.IGNORECASE)
_BULLET_PREFIX_RE = re.compile(r"^\s*(?:[-*\u2022]|\d+[.)])\s*")

# Canonical snake_case variable keys mapped from obvious keywords.
# Deliberately small: heuristic placeholder, not an NLP engine. Canonical
# keys matter because missing variables are deduplicated globally by key.
_VARIABLE_KEYWORDS: list[tuple[str, str]] = [
    ("age", "age"),
    ("year", "age"),
    ("male", "sex"),
    ("female", "sex"),
    ("sex", "sex"),
    ("pregnan", "pregnancy_status"),
    ("ecog", "ecog_performance_status"),
    ("performance status", "ecog_performance_status"),
    ("stage", "disease_stage"),
    ("biomarker", "biomarker_status"),
    ("her2", "biomarker_status"),
    ("mutation", "biomarker_status"),
    ("prior", "prior_treatment"),
    ("previously treated", "prior_treatment"),
    ("treatment-naive", "prior_treatment"),
    ("current", "current_treatment"),
    ("insulin use", "current_treatment"),
    ("creatinine clearance", "renal_function"),
    ("egfr", "renal_function"),
    ("renal", "renal_function"),
    ("hba1c", "hba1c"),
    ("cancer", "diagnosis"),
    ("diabetes", "diagnosis"),
    ("diagnos", "diagnosis"),
    ("confirmed", "diagnosis"),
    ("cardiac", "comorbidities"),
    ("metastases", "comorbidities"),
]


def _extract_required_variables(criterion_text: str) -> list[str]:
    lowered = criterion_text.lower()
    found: list[str] = []
    for keyword, variable in _VARIABLE_KEYWORDS:
        if keyword in lowered and variable not in found:
            found.append(variable)
    return found


def parse_trial_criteria_from_text(
    trial_id: str, trial_text: str
) -> tuple[TrialContext, list[Criterion]]:
    """Parse raw trial text into (TrialContext, list[Criterion]).

    Deterministic offline fallback:
    - lines before the first "Inclusion Criteria:"/"Exclusion Criteria:"
      header become the trial description (context/relevance only — the
      locked architecture forbids deriving blocking criteria from it);
    - lines under each header are split into individual criteria (bullet
      or plain lines), typed by their section, with stable sequential ids
      ``{trial_id}-INC-01`` / ``{trial_id}-EXC-01``;
    - obvious required_variables come from a small keyword map;
    - a missing exclusion (or inclusion) section simply yields no
      criteria of that type.

    Always returns Pydantic-validated objects. No LLM, no network.

    TODO: replace the heuristics with LLM structured extraction using
    prompts/criteria_parsing.md (schema-constrained JSON with normalized
    meanings and explicit thresholds/time windows), keeping this exact
    signature.
    """
    description_lines: list[str] = []
    inclusion_texts: list[str] = []
    exclusion_texts: list[str] = []

    section: list[str] = description_lines
    for raw_line in trial_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if _INCLUSION_HEADER_RE.match(line):
            section = inclusion_texts
            continue
        if _EXCLUSION_HEADER_RE.match(line):
            section = exclusion_texts
            continue
        section.append(_BULLET_PREFIX_RE.sub("", line).strip())

    criteria: list[Criterion] = []
    for texts, ctype, tag in (
        (inclusion_texts, CriterionType.inclusion, "INC"),
        (exclusion_texts, CriterionType.exclusion, "EXC"),
    ):
        for i, text in enumerate(texts, start=1):
            criteria.append(
                Criterion(
                    criterion_id=f"{trial_id}-{tag}-{i:02d}",
                    trial_id=trial_id,
                    criterion_type=ctype,
                    text=text,
                    required_variables=_extract_required_variables(text),
                )
            )

    context = TrialContext(
        trial_id=trial_id,
        description=" ".join(description_lines),
    )
    return context, criteria


def parse_criteria(protocol: TrialProtocol) -> list[Criterion]:
    """Split a protocol's raw eligibility text into structured criteria.

    Thin wrapper over :func:`parse_trial_criteria_from_text` using the
    protocol's ``eligibility_criteria_raw``.

    TODO: LLM-based parsing will also use protocol title/description for
    normalized meanings (never for new blocking criteria).
    """
    _, criteria = parse_trial_criteria_from_text(
        protocol.trial_id, protocol.eligibility_criteria_raw
    )
    return criteria
