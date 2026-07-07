"""Criterion Matching Agent.

Responsibility: judge each criterion against the evidence context and emit
a ``criterion_match_status`` (met / unmet / unknown / conflict /
not_applicable). The mapping from status to eligibility effect is NOT done
here; that is the pure rule ``rules.derive_eligibility_effect`` applied by
the Eligibility State Tracker.
"""

from __future__ import annotations

from models import Criterion, CriterionMatchStatus, EvidenceContext


def match_criterion(
    criterion: Criterion, evidence: EvidenceContext
) -> CriterionMatchStatus:
    """Judge one criterion from its evidence.

    TODO: LLM call comparing evidence against the criterion text; return
    conflict when sources disagree, unknown when required variables are
    absent, not_applicable when the criterion does not apply.
    """
    raise NotImplementedError("LLM-based matching not implemented in skeleton")
