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
    value = re.sub(
        r"\b(days?|weeks?|months?|years?)[-\s]+old\b",
        lambda match: match.group(1),
        value,
    )
    value = re.sub(r"\byrs?\b", "year", value)
    value = re.sub(r"\bper\b", "/", value)
    value = re.sub(r"\b(litres?|liters?)\b", "l", value)
    value = re.sub(r"\bdays\b", "day", value)
    value = re.sub(r"\bweeks\b", "week", value)
    value = re.sub(r"\bmonths\b", "month", value)
    value = re.sub(r"\byears\b", "year", value)
    value = re.sub(r"\bcycles\b", "cycle", value)
    value = re.sub(r"\bscores\b", "score", value)
    value = re.sub(r"\bpoints?\b", "score", value)
    compact = "".join(
        character
        for character in value
        if not character.isspace() and character not in {"^"}
    )
    return re.sub(r"^x(?=10)", "", compact)


def units_equivalent(left: str, right: str) -> bool:
    """Accept notation-only differences, never conversions between units."""

    return normalized_unit(left) == normalized_unit(right)


def converted_value(
    value: float,
    *,
    source_unit: str,
    target_unit: str,
) -> float | None:
    """Convert only fixed time-unit relationships used by eligibility rules."""

    source = normalized_unit(source_unit)
    target = normalized_unit(target_unit)
    if source == target:
        return value
    fixed_time_units = {
        "hour": 1.0,
        "day": 24.0,
        "week": 24.0 * 7.0,
    }
    calendar_age_units = {
        "month": 1.0,
        "year": 12.0,
    }
    for factors in (fixed_time_units, calendar_age_units):
        if source in factors and target in factors:
            return value * factors[source] / factors[target]
    return None
