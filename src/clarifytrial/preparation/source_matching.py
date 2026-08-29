"""Locate model-cited text without treating whitespace as medical meaning."""

from __future__ import annotations

import unicodedata
import re
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
        if (
            original == "\\"
            and position + 1 < len(text)
            and text[position + 1] in r"<>*_[]()#+-.!"
        ):
            continue
        normalized = unicodedata.normalize("NFKC", original).casefold()
        for character in normalized:
            if character.isspace() or unicodedata.category(character) == "Cf":
                continue
            characters.append(character)
            positions.append(position)
    return "".join(characters), positions


def comparison_text(text: str) -> str:
    """Normalize Unicode and ignore layout whitespace and Markdown escapes."""

    return _comparison_characters(text)[0]


def _context_tokens(text: str) -> set[str]:
    ignored = {
        "and",
        "for",
        "from",
        "have",
        "must",
        "that",
        "the",
        "their",
        "this",
        "with",
    }
    return {
        item
        for item in re.findall(
            r"[0-9a-z가-힣]+",
            unicodedata.normalize("NFKC", text).casefold(),
        )
        if len(item) >= 3 and item not in ignored
    }


def _local_context(text: str, start: int, end: int) -> str:
    left_candidates = [
        text.rfind("\n", 0, start),
        text.rfind(". ", 0, start),
        text.rfind("; ", 0, start),
    ]
    left = max(left_candidates) + 1
    right_candidates = [
        value
        for value in (
            text.find("\n", end),
            text.find(". ", end),
            text.find("; ", end),
        )
        if value >= 0
    ]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    return text[left:right]


def resolve_source_span(
    source_text: str,
    source_quote: str,
    *,
    approximate_start_char: int | None = None,
    approximate_end_char: int | None = None,
    occurrence_index: int | None = None,
    context_hint: str | None = None,
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

    if occurrence_index is not None:
        if occurrence_index < 0:
            raise SourceValidationError(
                "source quote occurrence index is outside the available matches"
            )
        match = matches[min(occurrence_index, len(matches) - 1)]
        method = (
            "normalized_occurrence_match"
            if occurrence_index < len(matches)
            else "normalized_reused_occurrence_match"
        )
    elif len(matches) > 1:
        selected_by_context = False
        hint_tokens = _context_tokens(context_hint or "")
        if hint_tokens:
            context_scores = []
            for candidate in matches:
                source_start = source_positions[candidate]
                source_end = source_positions[candidate + len(quote_key) - 1] + 1
                surrounding = _local_context(
                    source_text,
                    source_start,
                    source_end,
                )
                context_scores.append(
                    len(hint_tokens & _context_tokens(surrounding))
                )
            best = max(context_scores)
            if best > 0 and context_scores.count(best) == 1:
                match = matches[context_scores.index(best)]
                method = "normalized_context_match"
                selected_by_context = True
        if not selected_by_context:
            if approximate_start_char is None:
                raise SourceValidationError(
                    "quoted source text appears more than once; provide an approximate location"
                )
            distances = [
                abs(source_positions[candidate] - approximate_start_char)
                for candidate in matches
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
