"""Conservative normalization for equivalent measurement-unit spelling."""

from __future__ import annotations

import re
import unicodedata


def normalized_unit(unit: str) -> str:
    """Normalize spelling and symbols without converting measurement values."""

    value = unicodedata.normalize("NFKC", unit).casefold()
    replacements = {
        "percentage": "%",
        "percent": "%",
        "퍼센트": "%",
        "×": "x",
        "µ": "u",
        "μ": "u",
    }
    for before, after in replacements.items():
        value = value.replace(before, after)
    value = value.replace("\\", "")
    value = re.sub(r"\bper\b", "/", value)
    value = re.sub(r"\b(litres?|liters?)\b", "l", value)
    value = re.sub(r"\bdays\b", "day", value)
    value = re.sub(r"\bweeks\b", "week", value)
    value = re.sub(r"\byears\b", "year", value)
    compact = "".join(
        character
        for character in value
        if not character.isspace() and character not in {"^"}
    )
    return re.sub(r"^x(?=10)", "", compact)


def units_equivalent(left: str, right: str) -> bool:
    """Accept notation-only differences, never conversions between units."""

    return normalized_unit(left) == normalized_unit(right)
