"""Verify structured values that can change a screening decision."""

from __future__ import annotations

import math
import re
import unicodedata
from datetime import date

from ..contracts import (
    ComparisonOperator,
    EvidenceSourceType,
    VerificationStatus,
)
from ..measurements import normalized_unit
from .contracts import PatientFactDraft, TrialCriterionDraft
from .source_matching import SourceValidationError


_NUMBER_PATTERN = re.compile(
    r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\w.])"
)


def _numbers(text: str) -> list[float]:
    return [
        float(match.group(0).replace(",", ""))
        for match in _NUMBER_PATTERN.finditer(text)
    ]


def _number_is_present(expected: float, text: str) -> bool:
    return any(
        math.isclose(expected, observed, rel_tol=1e-9, abs_tol=1e-9)
        for observed in _numbers(text)
    )


def _unit_is_present(unit: str, source_text: str) -> bool:
    return normalized_unit(unit) in normalized_unit(source_text)


_ISO_DATE = re.compile(r"(?<!\d)(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)")
_KOREAN_DATE = re.compile(
    r"(?<!\d)(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일"
)


def _explicit_dates(text: str) -> set[date]:
    values: set[date] = set()
    for pattern in (_ISO_DATE, _KOREAN_DATE):
        for year, month, day in pattern.findall(text):
            try:
                values.add(date(int(year), int(month), int(day)))
            except ValueError:
                continue
    return values


_OPERATOR_PATTERNS: dict[ComparisonOperator, tuple[re.Pattern[str], ...]] = {
    ComparisonOperator.LTE: tuple(
        re.compile(pattern)
        for pattern in (
            r"less\s+than\s+or\s+equal",
            r"at\s+most",
            r"no\s+more\s+than",
            r"not\s+greater\s+than",
            r"<=|≤|이하|최대",
        )
    ),
    ComparisonOperator.GTE: tuple(
        re.compile(pattern)
        for pattern in (
            r"greater\s+than\s+or\s+equal",
            r"at\s+least",
            r"no\s+less\s+than",
            r"not\s+less\s+than",
            r">=|≥|이상|최소",
        )
    ),
    ComparisonOperator.LT: tuple(
        re.compile(pattern)
        for pattern in (
            r"less\s+than(?!\s+or\s+equal)",
            r"\bbelow\b",
            r"\bunder\b",
            r"(?<![<])<(?![=])|미만",
        )
    ),
    ComparisonOperator.GT: tuple(
        re.compile(pattern)
        for pattern in (
            r"greater\s+than(?!\s+or\s+equal)",
            r"\babove\b",
            r"\bover\b",
            r"(?<![>])>(?![=])|초과",
        )
    ),
    ComparisonOperator.EQ: tuple(
        re.compile(pattern)
        for pattern in (
            r"\bequal(?:s|\s+to)?\b",
            r"\bexactly\b",
            r"(?<![<>])=(?!=)|동일|같아야",
        )
    ),
}


def _supported_operators(source_text: str) -> set[ComparisonOperator]:
    normalized = unicodedata.normalize("NFKC", source_text).casefold()
    return {
        operator
        for operator, patterns in _OPERATOR_PATTERNS.items()
        if any(pattern.search(normalized) for pattern in patterns)
    }


_NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
}
_DURATION_PATTERN = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?|"
    + "|".join(_NUMBER_WORDS)
    + r")\s*(days?|weeks?|일|주)(?!\w)",
    re.IGNORECASE,
)


def _duration_days(source_text: str) -> set[int]:
    values: set[int] = set()
    for raw_value, raw_unit in _DURATION_PATTERN.findall(source_text):
        if raw_value.casefold() in _NUMBER_WORDS:
            numeric = float(_NUMBER_WORDS[raw_value.casefold()])
        else:
            numeric = float(raw_value)
        if raw_unit.casefold() in {"week", "weeks", "주"}:
            numeric *= 7
        if numeric.is_integer():
            values.add(int(numeric))
    return values


_SOURCE_KEYWORDS: dict[EvidenceSourceType, tuple[str, ...]] = {
    EvidenceSourceType.MEDICAL_RECORD: (
        "medical record",
        "health record",
        "chart",
        "ehr",
        "의무기록",
        "진료기록",
    ),
    EvidenceSourceType.PATIENT_REPORT: (
        "patient report",
        "self-report",
        "self report",
        "환자 진술",
        "환자 보고",
    ),
    EvidenceSourceType.OFFICIAL_VERIFICATION: (
        "official",
        "central lab",
        "laboratory result",
        "lab result",
        "pathology",
        "imaging",
        "공식",
        "중앙검사",
        "검사 결과",
        "병리",
        "영상",
    ),
    EvidenceSourceType.SYNTHETIC_CASE: ("synthetic", "합성"),
}

_STATUS_KEYWORDS: dict[VerificationStatus, tuple[str, ...]] = {
    VerificationStatus.VERIFIED: (
        "verified",
        "confirmed",
        "official",
        "validated",
        "central lab",
        "laboratory result",
        "lab result",
        "확인",
        "공식",
        "중앙검사",
        "검사 결과",
    ),
    VerificationStatus.REPORTED: (
        "reported",
        "self-report",
        "self report",
        "진술",
        "보고",
    ),
    VerificationStatus.PENDING: ("pending", "awaiting", "대기"),
    VerificationStatus.CONFLICTING: (
        "conflicting",
        "discrepancy",
        "inconsistent",
        "불일치",
        "상충",
    ),
}


def _contains_keyword(source_text: str, keywords: tuple[str, ...]) -> bool:
    normalized = unicodedata.normalize("NFKC", source_text).casefold()
    return any(keyword.casefold() in normalized for keyword in keywords)


def validate_patient_fact_source(
    fact: PatientFactDraft,
    matched_source_text: str,
) -> None:
    """Verify patient values and explicit dates used downstream by code."""

    if fact.value is not None:
        if not _number_is_present(fact.value, matched_source_text):
            raise SourceValidationError(
                f"patient value {fact.value:g} is not present in the cited source"
            )
        assert fact.unit is not None
        if not _unit_is_present(fact.unit, matched_source_text):
            raise SourceValidationError(
                f"patient unit {fact.unit!r} is not present in the cited source"
            )

    source_dates = _explicit_dates(matched_source_text)
    if source_dates and fact.event_date is None:
        raise SourceValidationError(
            "patient fact omitted an explicit event date from the cited source"
        )
    if source_dates and fact.event_date not in source_dates:
        raise SourceValidationError(
            "patient event date does not match the cited source"
        )


def validate_trial_criterion_source(
    criterion: TrialCriterionDraft,
    matched_source_text: str,
) -> None:
    """Verify structured criterion fields that can change code-based judgment."""

    constraint = criterion.numeric_constraint
    if constraint is not None:
        if not _number_is_present(constraint.threshold, matched_source_text):
            raise SourceValidationError(
                f"criterion threshold {constraint.threshold:g} is not present in the cited source"
            )
        if not _unit_is_present(constraint.unit, matched_source_text):
            raise SourceValidationError(
                f"criterion unit {constraint.unit!r} is not present in the cited source"
            )
        supported = _supported_operators(matched_source_text)
        if constraint.operator not in supported:
            raise SourceValidationError(
                "criterion operator "
                f"{constraint.operator.value!r} is not supported by the cited source"
            )

    requirement = criterion.evidence_requirement
    if requirement is None:
        return
    if requirement.max_age_days is not None:
        supported_days = _duration_days(matched_source_text)
        if requirement.max_age_days not in supported_days:
            raise SourceValidationError(
                f"evidence age {requirement.max_age_days} days is not supported by the cited source"
            )
    for source_type in requirement.allowed_source_types or []:
        if not _contains_keyword(
            matched_source_text,
            _SOURCE_KEYWORDS[source_type],
        ):
            raise SourceValidationError(
                f"evidence source {source_type.value!r} is not supported by the cited source"
            )
    for status in requirement.allowed_verification_statuses or []:
        if not _contains_keyword(
            matched_source_text,
            _STATUS_KEYWORDS[status],
        ):
            raise SourceValidationError(
                f"verification status {status.value!r} is not supported by the cited source"
            )
