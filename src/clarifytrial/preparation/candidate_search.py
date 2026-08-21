"""Candidate-search interfaces used by natural-input preparation."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

from ..retrieval import (
    BM25Retriever,
    CriterionStore,
    SearchDocument,
    TrialGPTRuntimeSearch,
)
from .contracts import CandidateSearchHit, TrialProtocolSource


class CandidateSearch(Protocol):
    """Return trial sources without inspecting screening labels or hidden answers."""

    def search(
        self,
        search_conditions: Sequence[str],
        *,
        top_k: int,
    ) -> list[CandidateSearchHit]: ...


class InMemoryCandidateSearch:
    """Small dependency-free BM25 search for examples and connection tests.

    The research comparison uses the TrialGPT BM25 and MedCPT search adapter.
    This implementation exists so the complete source-to-output path can be
    tested without downloading a model or a large trial corpus.
    """

    retrieval_method = "local-bm25-connection-test"

    def __init__(self, sources: Iterable[TrialProtocolSource]) -> None:
        ordered = list(sources)
        by_id = {item.trial_id: item for item in ordered}
        if len(by_id) != len(ordered):
            raise ValueError("trial sources must not repeat trial_id")
        self._sources = by_id
        documents = [
            SearchDocument(
                document_id=f"trial:{item.trial_id}",
                trial_id=item.trial_id,
                criterion_id=f"trial:{item.trial_id}:all-text",
                criterion_type="trial_source",
                raw_text="\n".join(
                    [
                        item.title,
                        item.title,
                        item.title,
                        *item.conditions,
                        *item.conditions,
                        item.summary,
                        item.eligibility_text,
                    ]
                ),
                source_location=item.source_location,
            )
            for item in ordered
        ]
        self._retriever = BM25Retriever(CriterionStore(documents))

    def search(
        self,
        search_conditions: Sequence[str],
        *,
        top_k: int,
    ) -> list[CandidateSearchHit]:
        if top_k < 1:
            raise ValueError("top_k must be at least one")
        conditions = [item.strip() for item in search_conditions if item.strip()]
        if not conditions:
            raise ValueError("at least one non-empty search condition is required")
        depth = len(self._sources)
        scores: dict[str, float] = {}
        has_positive_match = False
        for condition_index, condition in enumerate(conditions):
            condition_weight = 1 / (condition_index + 1)
            for hit in self._retriever.search(condition, top_k=max(1, depth)):
                if hit.score > 0:
                    has_positive_match = True
                    scores[hit.document.trial_id] = scores.get(
                        hit.document.trial_id, 0.0
                    ) + condition_weight / (20 + hit.rank)
        if not has_positive_match:
            return []
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        return [
            CandidateSearchHit(
                rank=rank,
                score=score,
                retrieval_method=self.retrieval_method,
                source=self._sources[trial_id],
            )
            for rank, (trial_id, score) in enumerate(ranked[:top_k], start=1)
        ]


class TrialGPTCandidateSearch:
    """Adapter from the reproduced TrialGPT index to preparation trial sources."""

    def __init__(self, runtime_search: TrialGPTRuntimeSearch) -> None:
        self._runtime_search = runtime_search

    def search(
        self,
        search_conditions: Sequence[str],
        *,
        top_k: int,
    ) -> list[CandidateSearchHit]:
        runtime_hits = self._runtime_search.search(
            search_conditions,
            top_k=top_k,
        )
        config = self._runtime_search.config
        method_parts = []
        if config.bm25_weight > 0:
            method_parts.append("BM25")
        if config.medcpt_weight > 0:
            method_parts.append("MedCPT")
        retrieval_method = "TrialGPT " + "+".join(method_parts)
        result = []
        for item in runtime_hits:
            diseases = item.entry.metadata.get("diseases_list", [])
            if not isinstance(diseases, list):
                diseases = []
            result.append(
                CandidateSearchHit(
                    rank=item.rank,
                    score=item.score,
                    retrieval_method=retrieval_method,
                    source=TrialProtocolSource(
                        trial_id=item.entry.trial_id,
                        title=item.entry.title or item.entry.trial_id,
                        conditions=[str(value) for value in diseases],
                        summary=item.entry.text,
                        eligibility_text=item.entry.text,
                        source_location=(
                            f"{self._runtime_search.corpus_path}"
                            f"#trial={item.entry.trial_id}"
                        ),
                    ),
                )
            )
        return result
