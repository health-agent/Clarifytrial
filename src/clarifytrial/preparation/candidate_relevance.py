"""Filter retrieved trials by disease relevance before reading eligibility rules."""

from __future__ import annotations

from collections.abc import Sequence

from ..agents import CandidateRelevanceAgent
from ..trace import TraceRecorder
from .contracts import CandidateSearchHit


class CandidateRelevanceProtocolError(RuntimeError):
    """The relevance role omitted or invented a supplied trial identifier."""


def review_candidate_relevance(
    *,
    search_conditions: Sequence[str],
    candidate_hits: Sequence[CandidateSearchHit],
    reviewer: CandidateRelevanceAgent,
    requested_count: int,
    trace: TraceRecorder,
) -> list[CandidateSearchHit]:
    """Keep only trials that study the searched disease or a true parent disease."""

    if requested_count < 1:
        raise ValueError("requested_count must be at least one")
    if not candidate_hits:
        return []
    trial_ids = [item.source.trial_id for item in candidate_hits]
    response = reviewer.run(
        {
            "search_conditions": list(search_conditions),
            "candidates": [
                {
                    "trial_id": item.source.trial_id,
                    "title": item.source.title,
                    "declared_conditions": item.source.conditions,
                    "summary": item.source.summary[:1_500],
                }
                for item in candidate_hits
            ],
        },
        trace=trace,
        cycle=0,
        input_refs=trial_ids,
    ).output
    returned_ids = [item.trial_id for item in response.decisions]
    if set(returned_ids) != set(trial_ids) or len(returned_ids) != len(trial_ids):
        raise CandidateRelevanceProtocolError(
            "candidate relevance review must return every supplied trial exactly once"
        )
    decision_by_id = {item.trial_id: item for item in response.decisions}
    kept = [
        item
        for item in candidate_hits
        if decision_by_id[item.source.trial_id].relevant
    ][:requested_count]
    reranked = [
        item.model_copy(
            update={
                "rank": rank,
                "retrieval_method": (
                    f"{item.retrieval_method} + disease relevance review"
                ),
            }
        )
        for rank, item in enumerate(kept, start=1)
    ]
    trace.record(
        cycle=0,
        actor="candidate_relevance_review",
        event="candidate_trials_filtered",
        input_refs=trial_ids,
        output={
            "search_conditions": list(search_conditions),
            "kept_trial_ids": [item.source.trial_id for item in reranked],
            "removed": [
                {
                    "trial_id": trial_id,
                    "reason": decision_by_id[trial_id].reason,
                }
                for trial_id in trial_ids
                if not decision_by_id[trial_id].relevant
            ],
        },
    )
    return reranked


__all__ = [
    "CandidateRelevanceProtocolError",
    "review_candidate_relevance",
]
