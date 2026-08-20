"""Small deterministic Okapi BM25 implementation.

The first unit avoids a heavyweight search service. The interface can later be
backed by the TREC corpus and combined with MedCPT without changing agent code.
"""

from __future__ import annotations

import math
import re
from collections import Counter

from .models import SearchDocument, SearchHit
from .store import CriterionStore


TOKEN_PATTERN = re.compile(r"\w+", flags=re.UNICODE)


def tokenize(text: str) -> list[str]:
    return [token.casefold() for token in TOKEN_PATTERN.findall(text)]


class BM25Retriever:
    def __init__(self, store: CriterionStore, *, k1: float = 1.5, b: float = 0.75) -> None:
        if k1 <= 0:
            raise ValueError("k1 must be greater than zero")
        if not 0 <= b <= 1:
            raise ValueError("b must be between zero and one")
        self.store = store
        self.k1 = k1
        self.b = b
        self._documents = list(store)
        self._tokens = [tokenize(item.raw_text) for item in self._documents]
        self._term_counts = [Counter(tokens) for tokens in self._tokens]
        self._average_length = (
            sum(len(tokens) for tokens in self._tokens) / len(self._tokens)
            if self._tokens
            else 0.0
        )
        self._document_frequency: Counter[str] = Counter()
        for tokens in self._tokens:
            self._document_frequency.update(set(tokens))

    def search(self, query: str, *, top_k: int = 10) -> list[SearchHit]:
        if top_k < 1:
            raise ValueError("top_k must be at least one")
        query_terms = tokenize(query)
        scored = [
            (self._score(index, query_terms), document)
            for index, document in enumerate(self._documents)
        ]
        scored.sort(key=lambda item: (-item[0], item[1].document_id))
        return [
            SearchHit(rank=rank, score=score, document=document)
            for rank, (score, document) in enumerate(scored[:top_k], start=1)
        ]

    def _score(self, index: int, query_terms: list[str]) -> float:
        if not query_terms or not self._documents or self._average_length == 0:
            return 0.0
        counts = self._term_counts[index]
        document_length = len(self._tokens[index])
        score = 0.0
        for term in query_terms:
            frequency = counts[term]
            if frequency == 0:
                continue
            document_frequency = self._document_frequency[term]
            inverse_document_frequency = math.log(
                1
                + (len(self._documents) - document_frequency + 0.5)
                / (document_frequency + 0.5)
            )
            denominator = frequency + self.k1 * (
                1 - self.b + self.b * document_length / self._average_length
            )
            score += inverse_document_frequency * frequency * (self.k1 + 1) / denominator
        return score
