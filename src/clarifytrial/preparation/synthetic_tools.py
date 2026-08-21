"""Build the hidden synthetic tool environment after fact IDs are assigned."""

from __future__ import annotations

from collections.abc import Iterable

from ..environment import (
    HiddenFactAnswer,
    HiddenPatientEnvironment,
    PublicFactRequest,
    PublicQuestionCatalog,
    SyntheticInformationTools,
)
from .contracts import NaturalHiddenFactAnswer, PreparedScreeningCase


def build_synthetic_information_tools(
    prepared: PreparedScreeningCase,
    answers: Iterable[NaturalHiddenFactAnswer],
) -> SyntheticInformationTools:
    """Translate fact keys without exposing answer values during preparation."""

    rows = list(answers)
    answer_keys = [item.fact_key for item in rows]
    if len(answer_keys) != len(set(answer_keys)):
        raise ValueError("synthetic answers must not repeat fact_key")
    unknown = set(answer_keys) - set(prepared.fact_id_by_key)
    if unknown:
        raise ValueError(
            "synthetic answers refer to unused fact_key values: "
            + ", ".join(sorted(unknown))
        )
    public_requests = [
        PublicFactRequest(
            fact_id=item.fact_id,
            description=item.description,
            available_actions=tuple(item.acceptable_actions),
        )
        for item in prepared.screening_case.evidence_requests
    ]
    hidden_answers = [
        HiddenFactAnswer(
            fact_id=prepared.fact_id_by_key[item.fact_key],
            access_path=item.access_path,
            evidence=item.evidence,
        )
        for item in rows
    ]
    return SyntheticInformationTools(
        PublicQuestionCatalog(public_requests),
        HiddenPatientEnvironment(hidden_answers),
    )
