"""Locate model-cited text without treating whitespace as medical meaning."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass


class SourceValidationError(ValueError):
    """A proposed fact or criterion is not supported by its cited source."""


@dataclass(frozen=True, slots=True)
class SourceSpan:
    """A source location calculated by code rather than trusted from the model."""

    start_char: int
    end_char: int
    source_text: str
    match_method: str


def _comparison_characters(text: str) -> tuple[str, list[int]]:
    characters: list[str] = []
    positions: list[int] = []
    for position, original in enumerate(text):
        normalized = unicodedata.normalize("NFKC", original).casefold()
        for character in normalized:
            if character.isspace() or unicodedata.category(character) == "Cf":
                continue
            characters.append(character)
            positions.append(position)
    return "".join(characters), positions


def comparison_text(text: str) -> str:
    """Normalize Unicode and ignore layout-only whitespace."""

    return _comparison_characters(text)[0]


def resolve_source_span(
    source_text: str,
    source_quote: str,
    *,
    approximate_start_char: int | None = None,
    approximate_end_char: int | None = None,
) -> SourceSpan:
    """Find a quote while ignoring whitespace, line wrapping, and letter case.

    Optional model offsets are only hints. If they are wrong but the quote has
    one clear source match, the code-calculated location is used instead.
    """

    quote_key = comparison_text(source_quote)
    if not quote_key:
        raise SourceValidationError("source quote contains no searchable text")

    if approximate_start_char is not None and approximate_end_char is not None:
        if (
            0 <= approximate_start_char < approximate_end_char <= len(source_text)
            and comparison_text(
                source_text[approximate_start_char:approximate_end_char]
            )
            == quote_key
        ):
            return SourceSpan(
                start_char=approximate_start_char,
                end_char=approximate_end_char,
                source_text=source_text[
                    approximate_start_char:approximate_end_char
                ],
                match_method="normalized_offset_hint",
            )

    source_key, source_positions = _comparison_characters(source_text)
    matches: list[int] = []
    cursor = 0
    while True:
        found = source_key.find(quote_key, cursor)
        if found < 0:
            break
        matches.append(found)
        cursor = found + 1
    if not matches:
        raise SourceValidationError("quoted source text was not found")

    if len(matches) > 1:
        if approximate_start_char is None:
            raise SourceValidationError(
                "quoted source text appears more than once; provide an approximate location"
            )
        distances = [
            abs(source_positions[match] - approximate_start_char)
            for match in matches
        ]
        nearest = min(distances)
        if distances.count(nearest) > 1:
            raise SourceValidationError(
                "quoted source text has more than one equally close match"
            )
        match = matches[distances.index(nearest)]
        method = "normalized_nearest_match"
    else:
        match = matches[0]
        method = "normalized_unique_match"

    start_char = source_positions[match]
    end_char = source_positions[match + len(quote_key) - 1] + 1
    return SourceSpan(
        start_char=start_char,
        end_char=end_char,
        source_text=source_text[start_char:end_char],
        match_method=method,
    )
