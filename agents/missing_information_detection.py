"""Missing Information Detection Agent.

Responsibility: inspect unknown criterion states across ALL trials, extract
the missing variables (``missing_variable_key``), and maintain the global
missing variable pool. Deduplication is GLOBAL by missing_variable_key:
the same variable needed by criteria in different trials yields exactly one
pool item (locked invariant), via ``rules.deduplicate_missing_variables``.
"""

from __future__ import annotations

from models import CriterionState, GlobalMissingVariablePoolItem
from rules import deduplicate_missing_variables


def detect_missing_variables(
    criterion_states: list[CriterionState],
) -> dict[str, GlobalMissingVariablePoolItem]:
    """Build the deduplicated global missing variable pool.

    TODO: LLM step to name missing variables consistently (canonical
    missing_variable_key) before pooling; the pooling itself is the pure
    rule below.
    """
    return deduplicate_missing_variables(criterion_states)
