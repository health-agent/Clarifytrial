"""Notation-only normalization for structured clinical concept labels."""

from __future__ import annotations

import unicodedata


def normalized_concept(value: str) -> str:
    """Return a comparison key without changing the medical meaning.

    This accepts harmless label differences such as ``HbA1c``/``hba1c`` and
    ``platelet_count``/``Platelet count``. It deliberately does not expand
    abbreviations or map medical synonyms; those need an explicit terminology
    table rather than a string rule.
    """

    normalized = unicodedata.normalize("NFKC", value).casefold()
    return "".join(character for character in normalized if character.isalnum())


def concepts_equivalent(left: str | None, right: str | None) -> bool:
    """Whether two populated labels differ only in harmless notation."""

    if left is None or right is None:
        return False
    left_key = normalized_concept(left)
    right_key = normalized_concept(right)
    return bool(left_key) and left_key == right_key
