"""In-memory criterion document store for the first implementation unit."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from .models import SearchDocument


class CriterionStore:
    """A small read-only store with stable document identifiers."""

    def __init__(self, documents: Iterable[SearchDocument]) -> None:
        ordered = list(documents)
        by_id = {item.document_id: item for item in ordered}
        if len(by_id) != len(ordered):
            raise ValueError("document_id must be unique")
        self._documents = tuple(ordered)
        self._by_id = by_id

    def __len__(self) -> int:
        return len(self._documents)

    def __iter__(self) -> Iterator[SearchDocument]:
        return iter(self._documents)

    def get(self, document_id: str) -> SearchDocument:
        return self._by_id[document_id]
