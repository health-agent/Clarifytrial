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
    r"(?<![\w.])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?!\d|\.\d)"
)
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

_GENERIC_CONCEPT_WORDS = {
    "at",
    "current",
    "count",
    "diagnosis",
    "history",
    "level",
    "measurement",
    "result",
    "screening",
    "status",
    "test",
    "value",
}
_CONCEPT_ALIASES: dict[str, tuple[str, ...]] = {
    "absolute_neutrophil_count": ("absolute neutrophil count", "anc"),
    "body_mass_index": ("body mass index", "bmi"),
    "estimated_glomerular_filtration_rate": (
        "estimated glomerular filtration rate",
        "egfr",
    ),
    "hba1c": ("hba1c", "hb a1c", "hemoglobin a1c", "glycated hemoglobin"),
    "platelet_count": ("platelet count", "platelet", "platelets"),
}


def _numbers(text: str) -> list[float]:
    return [
        float(match.group(0).replace(",", ""))
        for match in _NUMBER_PATTERN.finditer(text)
    ]


def _number_is_present(expected: float, text: str) -> bool:
    numeric_match = any(
        math.isclose(expected, observed, rel_tol=1e-9, abs_tol=1e-9)
        for observed in _numbers(text)
    )
    if numeric_match:
        return True
    return any(
        math.isclose(expected, value, rel_tol=1e-9, abs_tol=1e-9)
        and re.search(rf"(?i)\b{word}\b", text)
        for word, value in _NUMBER_WORDS.items()
    )


def _concept_is_present(concept: str, source_text: str) -> bool:
    """Require lexical support without demanding identical spacing or casing."""

    source = unicodedata.normalize("NFKC", source_text).casefold()
    source_compact = re.sub(r"[^0-9a-z가-힣]+", "", source)
    concept_key = re.sub(
        r"[^0-9a-z가-힣]+",
        "_",
        unicodedata.normalize("NFKC", concept).casefold(),
    ).strip("_")
    if "duration" in concept_key and re.search(
        r"(?i)\b(?:\d+(?:\.\d+)?|"
        + "|".join(_NUMBER_WORDS)
        + r")[ -]?(?:hour|day|week|month|year)s?\b",
        source,
    ):
        return True
    if concept_key == "age" and re.search(
        r"(?i)(?:\baged?\b|\b(?:day|week|month|year)s?[-\s]+old\b)",
        source,
    ):
        return True
    phrases = {
        concept_key.replace("_", " "),
        *_CONCEPT_ALIASES.get(concept_key, ()),
    }
    tokens = [
        item
        for item in concept_key.split("_")
        if item and item not in _GENERIC_CONCEPT_WORDS
    ]
    if len(tokens) >= 2:
        phrases.add("".join(item[0] for item in tokens))
    for token in tokens:
        if len(token) >= 4 or token in {"age", "anc", "bmi", "egfr", "isi"}:
            phrases.add(token)
    return any(
        re.sub(r"[^0-9a-z가-힣]+", "", phrase.casefold()) in source_compact
        for phrase in phrases
        if phrase
    )


def _unit_is_present(
    unit: str,
    source_text: str,
    concept: str | None = None,
) -> bool:
    normalized = normalized_unit(unit)
    normalized_source = normalized_unit(source_text)
    if normalized in normalized_source:
        return True
    if "/" in normalized:
        parts = [item for item in normalized.split("/") if item]
        if len(parts) == 2 and "/" in normalized_source and all(
            item in normalized_source for item in parts
        ):
            return True
    if normalized == "xuln" and "uln" in normalized_source:
        return True
    if (
        concept
        and normalized == "score"
        and (
            any(item in concept.casefold() for item in ("score", "status"))
            or concept.casefold().strip() in {"ecog", "ecog-ps"}
        )
        and _concept_is_present(concept, source_text)
    ):
        return True
    return bool(
        concept
        and "age" in concept.casefold()
        and normalized == "year"
        and re.search(r"\baged?\b", source_text, re.IGNORECASE)
    )


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
            r"\bmaximum\b",
            r"\bwithin(?:\s+the)?(?:\s+(?:past|last))?\b",
            r"or\s+(?:less|lower|younger)",
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
            r"\bminimum\b",
            r"or\s+(?:greater|more|older)",
            r"or\s+over",
            r"(?:and|or)\s+higher",
            r">=|≥|이상|최소",
        )
    ),
    ComparisonOperator.LT: tuple(
        re.compile(pattern)
        for pattern in (
            r"less\s+than(?!\s+or\s+equal)",
            r"\bfewer\s+than\b",
            r"\bbelow\b",
            r"\bunder\b",
            r"(?<![<])<(?![=])|미만",
        )
    ),
    ComparisonOperator.GT: tuple(
        re.compile(pattern)
        for pattern in (
            r"greater\s+than(?!\s+or\s+equal)",
            r"\bmore\s+than\b",
            r"\bno\s+(?:occurrence|episode|use|receipt|treatment)"
            r"[^.;\n]{0,100}\bwithin\b",
            r"\bnot\s+(?:yet\s+)?improved\s+to\b[^\n]{0,120}(?:≤|<=)",
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


_RANGE_PATTERN = re.compile(
    r"(?:\bbetween\s+|\bfrom\s+)?"
    r"([-+]?(?:\d+(?:\.\d+)?))\s*"
    r"(?:[a-z가-힣]+(?:\s+old)?\s*)?"
    r"(?:-|–|—|\bto\b|\band\b)\s*"
    r"([-+]?(?:\d+(?:\.\d+)?))",
    re.IGNORECASE,
)


def _range_supports_operator(
    expected: float,
    operator: ComparisonOperator,
    source_text: str,
) -> bool:
    normalized = unicodedata.normalize("NFKC", source_text)
    for raw_lower, raw_upper in _RANGE_PATTERN.findall(normalized):
        lower = float(raw_lower)
        upper = float(raw_upper)
        if operator is ComparisonOperator.GTE and math.isclose(expected, lower):
            return True
        if operator is ComparisonOperator.LTE and math.isclose(expected, upper):
            return True
    return False


_DURATION_PATTERN = re.compile(
    r"(?<![\w.])(\d+(?:\.\d+)?|"
    + "|".join(_NUMBER_WORDS)
    + r")\s*(hours?|days?|weeks?|시간|일|주)(?!\w)",
    re.IGNORECASE,
)


def _duration_days(source_text: str) -> set[int]:
    values: set[int] = set()
    for raw_value, raw_unit in _DURATION_PATTERN.findall(source_text):
        if raw_value.casefold() in _NUMBER_WORDS:
            numeric = float(_NUMBER_WORDS[raw_value.casefold()])
        else:
            numeric = float(raw_value)
        unit = raw_unit.casefold()
        if unit in {"hour", "hours", "시간"}:
            numeric /= 24
        elif unit in {"week", "weeks", "주"}:
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
        "histopathology",
        "histopathologically",
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
        assert fact.concept is not None
        if not _concept_is_present(fact.concept, matched_source_text):
            raise SourceValidationError(
                f"patient concept {fact.concept!r} is not supported by the cited source"
            )
        if not _number_is_present(fact.value, matched_source_text):
            raise SourceValidationError(
                f"patient value {fact.value:g} is not present in the cited source"
            )
        assert fact.unit is not None
        if not _unit_is_present(fact.unit, matched_source_text, fact.concept):
            raise SourceValidationError(
                f"patient unit {fact.unit!r} is not present in the cited source"
            )

    source_dates = _explicit_dates(matched_source_text)
    if fact.event_date is not None and not source_dates:
        raise SourceValidationError(
            "patient event date is not present in the cited source"
        )
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
        if not _unit_is_present(
            constraint.unit,
            matched_source_text,
            constraint.concept,
        ):
            raise SourceValidationError(
                f"criterion unit {constraint.unit!r} is not present in the cited source"
            )
        supported = _supported_operators(matched_source_text)
        if (
            constraint.operator not in supported
            and not _range_supports_operator(
                constraint.threshold,
                constraint.operator,
                matched_source_text,
            )
        ):
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


def remove_unsupported_evidence_requirements(
    criterion: TrialCriterionDraft,
    matched_source_text: str,
    *,
    allow_remove_recency: bool = False,
) -> tuple[TrialCriterionDraft, list[str]]:
    """Remove model-added evidence rules that the quoted criterion does not state."""

    requirement = criterion.evidence_requirement
    if requirement is None:
        return criterion, []
    corrections: list[str] = []
    max_age_days = requirement.max_age_days
    if (
        allow_remove_recency
        and max_age_days is not None
        and max_age_days not in _duration_days(matched_source_text)
    ):
        max_age_days = None
        corrections.append(
            "복수 경로를 아직 해석하지 못한 원문에서 근거 범위를 벗어난 자료 유효기간을 제거했다."
        )
    allowed_source_types = requirement.allowed_source_types
    if allowed_source_types is not None:
        kept_sources = [
            item
            for item in allowed_source_types
            if _contains_keyword(matched_source_text, _SOURCE_KEYWORDS[item])
        ]
        if kept_sources != allowed_source_types:
            corrections.append("원문에 없는 자료 출처 제한을 제거했다.")
        allowed_source_types = kept_sources or None
    allowed_statuses = requirement.allowed_verification_statuses
    if allowed_statuses is not None:
        kept_statuses = [
            item
            for item in allowed_statuses
            if _contains_keyword(matched_source_text, _STATUS_KEYWORDS[item])
        ]
        if kept_statuses != allowed_statuses:
            corrections.append("원문에 없는 자료 확인 상태 제한을 제거했다.")
        allowed_statuses = kept_statuses or None
    if max_age_days is None and allowed_source_types is None and allowed_statuses is None:
        sanitized_requirement = None
    else:
        sanitized_requirement = requirement.model_copy(
            update={
                "max_age_days": max_age_days,
                "allowed_source_types": allowed_source_types,
                "allowed_verification_statuses": allowed_statuses,
            }
        )
    return (
        criterion.model_copy(
            update={"evidence_requirement": sanitized_requirement}
        ),
        corrections,
    )


def remove_unwritten_equality_constraint(
    criterion: TrialCriterionDraft,
    matched_source_text: str,
) -> tuple[TrialCriterionDraft, list[str]]:
    """Leave an equality-like timepoint to text judgment when equality is unwritten."""

    constraint = criterion.numeric_constraint
    if (
        constraint is None
        or constraint.operator is not ComparisonOperator.EQ
        or ComparisonOperator.EQ in _supported_operators(matched_source_text)
    ):
        return criterion, []
    return (
        criterion.model_copy(update={"numeric_constraint": None}),
        [
            "원문은 평가 시점을 설명하지만 값이 정확히 같아야 한다고 쓰지 않아 수치 같음 조건을 제거했다."
        ],
    )
