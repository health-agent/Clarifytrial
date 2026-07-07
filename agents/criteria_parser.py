"""Criteria Parser Agent.

Responsibility: parse a trial's raw eligibility criteria text
(``TrialProtocol.eligibility_criteria_raw``) into structured
``Criterion`` objects with ``criterion_type`` and ``required_variables``.

Source-agnostic: works on raw text regardless of where the protocol came
from (a future ClinicalTrials.gov API v2 adapter would only populate
``TrialProtocol``; this agent does not know about API response paths).
"""

from __future__ import annotations

from models import Criterion, TrialProtocol


def parse_criteria(protocol: TrialProtocol) -> list[Criterion]:
    """Split raw eligibility text into structured criteria.

    TODO: LLM call to segment inclusion/exclusion sections, extract one
    criterion per clause, classify criterion_type, and infer
    required_variables (e.g. "age", "ecog_performance_status").
    """
    raise NotImplementedError("LLM-based criteria parsing not implemented in skeleton")
