"""Missing Information Detection Agent.

Responsibility: inspect unknown criterion states across ALL trials, extract
the missing variables (``missing_variable_key``), and maintain the global
missing variable pool. Deduplication is GLOBAL by missing_variable_key:
the same variable needed by criteria in different trials yields exactly one
pool item (locked invariant), via ``rules.deduplicate_missing_variables``.

Missing information is NEVER negative evidence and no eligibility is
decided here — the pool only records what must be asked, with full
traceability back to the source trials and criteria.
"""

from __future__ import annotations

from models import (
    CriterionMatchStatus,
    CriterionState,
    GlobalMissingVariablePoolItem,
)
from rules import deduplicate_missing_variables

# Common screening variables get at least "medium" priority.
_SCREENING_VARIABLES = {
    "ecog_performance_status",
    "renal_function",
    "biomarker_status",
    "prior_treatment",
}


def _assign_priority(item: GlobalMissingVariablePoolItem) -> str:
    """Deterministic priority for one pool item.

    - high: the variable is needed by multiple criteria or multiple
      trials (answering it unblocks the most state at once);
    - medium: common screening variables;
    - low: everything else.

    Note: "tied to a blocking criterion" cannot boost priority here —
    unknown criteria are by definition not (yet) blocking, and the
    existing models carry no impact score. TODO: revisit once criterion
    impact metadata exists.
    """
    if len(item.affected_criterion_ids) > 1 or len(item.affected_trial_ids) > 1:
        return "high"
    if item.missing_variable_key in _SCREENING_VARIABLES:
        return "medium"
    return "low"


def build_global_missing_variable_pool(
    criterion_states: list[CriterionState],
) -> dict[str, GlobalMissingVariablePoolItem]:
    """Build the session-level missing-variable pool from criterion states.

    - only states with ``criterion_match_status == unknown`` AND at least
      one ``missing_variable_key`` contribute (unknown without keys is
      tolerated and simply skipped);
    - deduplication and trial/criterion traceability come from the
      existing pure rule ``rules.deduplicate_missing_variables`` — not
      reimplemented here;
    - each pool item is enriched with a description built from the
      contributing states' evidence summaries (when available) and a
      deterministic priority.

    Deterministic and offline; decides nothing about eligibility.
    """
    unknown_states = [
        s
        for s in criterion_states
        if s.criterion_match_status == CriterionMatchStatus.unknown
        and s.missing_variable_keys
    ]

    pool = deduplicate_missing_variables(unknown_states)

    # Enrich with evidence-derived descriptions and priorities.
    evidence_by_key: dict[str, list[str]] = {}
    for state in unknown_states:
        summary = (
            "; ".join(sentence.text for sentence in state.evidence.sentences)
            if state.evidence and state.evidence.sentences
            else ""
        )
        for key in state.missing_variable_keys:
            if summary:
                evidence_by_key.setdefault(key, []).append(
                    f"{state.criterion_id}: {summary}"
                )

    for key, item in pool.items():
        item.priority = _assign_priority(item)
        reasons = evidence_by_key.get(key)
        item.description = (
            " | ".join(reasons)
            if reasons
            else f"Required by {len(item.affected_criterion_ids)} criterion/criteria; "
            f"not present in the patient profile."
        )
    return pool


def detect_missing_variables(
    criterion_states: list[CriterionState],
) -> dict[str, GlobalMissingVariablePoolItem]:
    """Build the deduplicated global missing variable pool.

    Kept for backward compatibility; prefer
    :func:`build_global_missing_variable_pool`, which filters to unknown
    states and adds priority/traceability enrichment.

    TODO: LLM step to name missing variables consistently (canonical
    missing_variable_key) before pooling; the pooling itself is the pure
    rule.
    """
    return deduplicate_missing_variables(criterion_states)
