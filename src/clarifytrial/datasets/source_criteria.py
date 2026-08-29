"""Conservative structuring of objective criteria from a public trial snapshot.

The extractor intentionally keeps only fields and source lines that can be
represented without guessing clinical meaning.  Unsupported compound prose is
counted and left out of the executable benchmark instead of being flattened.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import Field, model_validator

from ..contracts import (
    ContractModel,
    CriterionKind,
    CriterionLogic,
    CriterionLogicOperator,
)
from ..preparation.team_trials import TeamTrialRecord
from .synthetic_evidence import AcquisitionModeValue


class ExplicitLogicGroup(ContractModel):
    """One unambiguous N-of-M source block declared in the benchmark config."""

    trial_id: str = Field(min_length=1)
    label: str = Field(min_length=1)
    operator: Literal["all", "any", "at_least"]
    source_line_numbers: list[int] = Field(min_length=1)
    minimum_required: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def operator_and_count_match(self) -> "ExplicitLogicGroup":
        if len(self.source_line_numbers) != len(set(self.source_line_numbers)):
            raise ValueError("logic source lines must be unique")
        if self.operator == "at_least":
            if self.minimum_required is None:
                raise ValueError("at_least logic needs minimum_required")
            if self.minimum_required > len(self.source_line_numbers):
                raise ValueError("minimum_required exceeds source line count")
        elif self.minimum_required is not None:
            raise ValueError("only at_least logic can set minimum_required")
        return self


_CONCEPT_PATTERNS: tuple[tuple[str, str, str, str], ...] = (
    ("hba1c", "HbA1c", "%", r"\bhba1c\b"),
    ("egfr", "eGFR", "mL/min/1.73m2", r"\begfr\b"),
    ("ecog", "ECOG 활동수준", "score", r"\becog\b"),
    ("body_mass_index", "BMI", "kg/m2", r"\b(?:bmi|body mass index)\b"),
    ("platelet_count", "혈소판 수", "source-unit", r"\bplatelet"),
    (
        "absolute_neutrophil_count",
        "절대호중구수",
        "source-unit",
        r"\b(?:absolute neutrophil count|anc)\b",
    ),
    ("hemoglobin", "혈색소", "source-unit", r"\bhemoglobin\b"),
    ("serum_creatinine", "혈청 크레아티닌", "source-unit", r"\bcreatinine\b"),
    ("bilirubin", "빌리루빈", "source-unit", r"\bbilirubin\b"),
    ("alt", "ALT", "xULN", r"\balt\b|alanine aminotransferase"),
    ("ast", "AST", "xULN", r"\bast\b|aspartate aminotransferase"),
    ("heart_rate", "심박수", "bpm", r"\bheart rate\b"),
    ("body_weight", "체중", "kg", r"\b(?:body )?weight\b"),
    ("temperature", "체온", "C", r"\btemperature\b"),
)

_COMPARATOR_RE = re.compile(r"(?P<operator>>=|<=|>|<)\s*(?P<value>\d+(?:\.\d+)?)")
_UNSAFE_CONNECTOR_RE = re.compile(r"\b(?:unless|except|whichever|and/or)\b", re.I)
_COMPOUND_NUMERIC_RE = re.compile(r"\b(?:and|or)\b|;", re.I)
_PREGNANCY_RE = re.compile(r"\b(?:pregnan\w*|lactat\w*|breastfeed\w*)\b", re.I)
_INFECTION_RE = re.compile(
    r"(?:\b(?:active|severe|uncontrolled)\b.{0,45}\binfection\b|"
    r"\binfection\b.{0,45}\b(?:active|severe|uncontrolled)\b)",
    re.I,
)
_EXPLICIT_AGE_LINE_RE = re.compile(
    r"(?:\b(?:adults?|children)\b.{0,80}\b(?:\d+|eighteen)\b|"
    r"\b(?:\d+|eighteen)\b.{0,35}\b(?:years?\s+old|years?\s+of\s+age)\b|"
    r"\bages?\s+(?:\d+|eighteen)\b)",
    re.I,
)


def _normalize_comparators(text: str) -> str:
    return (
        text.replace("\\>=", ">=")
        .replace("\\<=", "<=")
        .replace("\\>", ">")
        .replace("\\<", "<")
        .replace("≥", ">=")
        .replace("≤", "<=")
        .replace("=<", "<=")
        .replace("=>", ">=")
    )


def _sectioned_lines(text: str) -> list[tuple[int, CriterionKind | None, str]]:
    section: CriterionKind | None = None
    rows = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        lower = line.casefold()
        if "exclusion criteria" in lower and len(line) < 100:
            section = CriterionKind.EXCLUSION
            continue
        if "inclusion criteria" in lower and len(line) < 100:
            section = CriterionKind.INCLUSION
            continue
        rows.append((line_number, section, line))
    return rows


def _age_years(raw: str | None) -> float | None:
    if raw is None or not raw.strip():
        return None
    match = re.fullmatch(
        r"\s*(\d+(?:\.\d+)?)\s*(day|week|month|year)s?\s*",
        raw,
        re.I,
    )
    if match is None:
        return None
    value = float(match.group(1))
    unit = match.group(2).casefold()
    factors = {"day": 1 / 365.25, "week": 7 / 365.25, "month": 1 / 12, "year": 1}
    years = value * factors[unit]
    return years if 0 <= years <= 120 else None


def plausible_numeric_range(
    fact_code: str,
    unit: str,
) -> tuple[float, float] | None:
    """Return broad human ranges that expose broken parses and synthetic values."""

    code = fact_code.casefold()
    normalized_unit = unit.casefold()
    if code == "age_years":
        return 0.0, 100.0
    if code.startswith("ecog"):
        return 0.0, 5.0
    if code.startswith("hba1c"):
        return 2.0, 30.0
    if code.startswith("egfr"):
        return 0.0, 300.0
    if code.startswith("body_mass_index"):
        return 5.0, 100.0
    if code.startswith("body_weight"):
        return 1.0, 500.0
    if code.startswith("heart_rate"):
        return 0.0, 300.0
    if code.startswith("temperature"):
        return 20.0, 50.0
    if code.startswith(("alt", "ast")):
        return 0.0, 100.0
    if code.startswith("hemoglobin"):
        return (0.0, 300.0) if normalized_unit == "g/l" else (0.0, 30.0)
    if code.startswith("absolute_neutrophil_count"):
        if normalized_unit == "10^9/l":
            return 0.0, 100.0
        return 0.0, 100_000.0
    if code.startswith("platelet_count"):
        if normalized_unit == "10^9/l":
            return 0.0, 2_000.0
        return 0.0, 2_000_000.0
    if code.startswith(("serum_creatinine", "bilirubin")):
        if normalized_unit == "umol/l":
            return 0.0, 5_000.0
        return 0.0, 100.0
    return None


def _source_unit(line: str, fallback: str) -> str | None:
    normalized = line.casefold().replace("²", "2").replace("⁹", "9")
    if fallback != "source-unit":
        return fallback
    if "10^9/l" in normalized or "10 9/l" in normalized or "10x9/l" in normalized:
        return "10^9/L"
    if "/μl" in normalized or "/µl" in normalized or "/ul" in normalized:
        return "/uL"
    if "/mm3" in normalized or "/mm³" in line.casefold():
        return "/mm3"
    if "g/dl" in normalized:
        return "g/dL"
    if "g/l" in normalized:
        return "g/L"
    if "mg/dl" in normalized:
        return "mg/dL"
    if "μmol/l" in normalized or "µmol/l" in normalized or "umol/l" in normalized:
        return "umol/L"
    return None


def _source_url(trial_id: str) -> str:
    return f"https://clinicaltrials.gov/study/{trial_id}"


def _criterion_row(
    *,
    trial_id: str,
    group_id: str,
    sequence: int,
    source_text: str,
    source_line_number: int,
    source_field: str,
    kind: CriterionKind,
    fact_code: str,
    fact_description: str,
    operator: str,
    threshold: float,
    unit: str,
    acquisition_mode: AcquisitionModeValue,
    confidence: str,
) -> dict[str, Any]:
    return {
        "criterion_id": f"{trial_id}:criterion:{sequence:02d}",
        "group_id": group_id,
        "nct_id": trial_id,
        "kind": kind.value,
        "candidate_id": f"{trial_id}:candidate:{sequence:02d}",
        "source_text": source_text,
        "line_number": source_line_number,
        "source_field": source_field,
        "source_location": f"{_source_url(trial_id)}#participation-criteria",
        "confidence": confidence,
        "fact_code": fact_code,
        "fact_description": fact_description,
        "criterion_summary": source_text,
        "expected_value": None,
        "operator": operator,
        "threshold": threshold,
        "unit": unit,
        "acquisition_mode": acquisition_mode,
    }


def _age_rows(
    record: TeamTrialRecord,
    group_id: str,
) -> list[dict[str, Any]]:
    minimum = _age_years(record.minimum_age)
    maximum = _age_years(record.maximum_age)
    statement = (
        "ClinicalTrials.gov age fields: "
        f"minimum={record.minimum_age or 'not stated'}; "
        f"maximum={record.maximum_age or 'not stated'}"
    )
    rows = []
    if minimum is not None:
        rows.append(
            _criterion_row(
                trial_id=record.nct_id,
                group_id=group_id,
                sequence=0,
                source_text=statement,
                source_line_number=0,
                source_field="minimum_age",
                kind=CriterionKind.INCLUSION,
                fact_code="age_years",
                fact_description="현재 나이",
                operator="gte",
                threshold=minimum,
                unit="years",
                acquisition_mode="internal_record",
                confidence="registry_structured_field",
            )
        )
    if maximum is not None:
        rows.append(
            _criterion_row(
                trial_id=record.nct_id,
                group_id=group_id,
                sequence=0,
                source_text=statement,
                source_line_number=0,
                source_field="maximum_age",
                kind=CriterionKind.INCLUSION,
                fact_code="age_years",
                fact_description="현재 나이",
                operator="lte",
                threshold=maximum,
                unit="years",
                acquisition_mode="internal_record",
                confidence="registry_structured_field",
            )
        )
    return rows


def _keyword_exclusion_row(
    record: TeamTrialRecord,
    group_id: str,
    *,
    fact_code: str,
    description: str,
    pattern: re.Pattern[str],
    acquisition_mode: AcquisitionModeValue,
) -> dict[str, Any] | None:
    for line_number, section, line in _sectioned_lines(record.eligibility_text):
        if section is not CriterionKind.EXCLUSION:
            continue
        if pattern.search(line) is None or _UNSAFE_CONNECTOR_RE.search(line):
            continue
        return _criterion_row(
            trial_id=record.nct_id,
            group_id=group_id,
            sequence=0,
            source_text=line,
            source_line_number=line_number,
            source_field="eligibility_text",
            kind=CriterionKind.EXCLUSION,
            fact_code=fact_code,
            fact_description=description,
            operator="eq",
            threshold=1.0,
            unit="bool",
            acquisition_mode=acquisition_mode,
            confidence="exact_keyword_exclusion",
        )
    return None


def _numeric_rows(
    record: TeamTrialRecord,
    group_id: str,
) -> list[dict[str, Any]]:
    rows = []
    seen: set[tuple[str, str, float, str, str]] = set()
    for line_number, section, raw_line in _sectioned_lines(record.eligibility_text):
        if (
            section is None
            or _UNSAFE_CONNECTOR_RE.search(raw_line)
            or _COMPOUND_NUMERIC_RE.search(raw_line)
        ):
            continue
        line = _normalize_comparators(raw_line)
        if re.search(r"\d,\d", line):
            continue
        comparisons = list(_COMPARATOR_RE.finditer(line))
        if len(comparisons) != 1:
            continue
        concept_matches = [
            (fact_code, description, fallback_unit)
            for fact_code, description, fallback_unit, pattern in _CONCEPT_PATTERNS
            if re.search(pattern, line, re.I)
        ]
        if len(concept_matches) != 1:
            continue
        fact_code, description, fallback_unit = concept_matches[0]
        unit = _source_unit(line, fallback_unit)
        if unit is None:
            continue
        match = comparisons[0]
        operator = {">=": "gte", ">": "gt", "<=": "lte", "<": "lt"}[
            match.group("operator")
        ]
        threshold = float(match.group("value"))
        unit_key = re.sub(r"[^a-z0-9]+", "_", unit.casefold()).strip("_")
        normalized_fact_code = (
            fact_code if not unit_key else f"{fact_code}_{unit_key}"
        )
        plausible_range = plausible_numeric_range(normalized_fact_code, unit)
        if plausible_range is not None and not (
            plausible_range[0] <= threshold <= plausible_range[1]
        ):
            continue
        key = (normalized_fact_code, operator, threshold, unit, section.value)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            _criterion_row(
                trial_id=record.nct_id,
                group_id=group_id,
                sequence=0,
                source_text=raw_line,
                source_line_number=line_number,
                source_field="eligibility_text",
                kind=section,
                fact_code=normalized_fact_code,
                fact_description=description,
                operator=operator,
                threshold=threshold,
                unit=unit,
                acquisition_mode="existing_official_result",
                confidence="single_numeric_expression",
            )
        )
    return rows


def _fallback_predicate_rows(
    record: TeamTrialRecord,
    group_id: str,
) -> list[dict[str, Any]]:
    rows = []
    for line_number, section, line in _sectioned_lines(record.eligibility_text):
        if section is None or not (15 <= len(line) <= 240):
            continue
        if _UNSAFE_CONNECTOR_RE.search(line) or _COMPOUND_NUMERIC_RE.search(line):
            continue
        lower = line.casefold()
        normalized = _normalize_comparators(line)
        if lower.endswith(":") or _COMPARATOR_RE.search(normalized):
            continue
        if any(
            token in lower
            for token in (
                "criteria",
                "eligible disease",
                "exception",
                "following",
                "investigator",
                "researcher",
                "informed consent",
                "willing",
                "able to",
                "at least",
                "less than",
                "more than",
                "within",
            )
        ):
            continue
        fact_code = f"{record.nct_id.casefold()}_source_line_{line_number}"
        description = f"다음 시험 조건에 해당하는지: {line}"
        rows.append(
            _criterion_row(
                trial_id=record.nct_id,
                group_id=group_id,
                sequence=0,
                source_text=line,
                source_line_number=line_number,
                source_field="eligibility_text",
                kind=section,
                fact_code=fact_code,
                fact_description=description,
                operator="eq",
                threshold=1.0,
                unit="bool",
                acquisition_mode=(
                    "patient_report"
                    if any(token in lower for token in ("history", "current", "prior"))
                    else "internal_record"
                ),
                confidence="simple_source_predicate",
            )
        )
    return rows


def _logic_rows(
    record: TeamTrialRecord,
    group_id: str,
    declarations: Sequence[ExplicitLogicGroup],
) -> tuple[list[dict[str, Any]], list[tuple[ExplicitLogicGroup, list[str]]]]:
    lines = {
        line_number: line
        for line_number, _, line in _sectioned_lines(record.eligibility_text)
    }
    rows = []
    groups = []
    for declaration in declarations:
        if declaration.trial_id != record.nct_id:
            continue
        criterion_ids = []
        for line_number in declaration.source_line_numbers:
            line = lines.get(line_number)
            if line is None:
                raise ValueError(
                    f"logic source line {line_number} is absent from {record.nct_id}"
                )
            row = _criterion_row(
                trial_id=record.nct_id,
                group_id=group_id,
                sequence=0,
                source_text=line,
                source_line_number=line_number,
                source_field="eligibility_text",
                kind=CriterionKind.INCLUSION,
                fact_code=(
                    f"{record.nct_id.casefold()}_logic_line_{line_number}"
                ),
                fact_description=f"{declaration.label}: {line}",
                operator="eq",
                threshold=1.0,
                unit="bool",
                acquisition_mode="internal_record",
                confidence="declared_explicit_logic",
            )
            rows.append(row)
            criterion_ids.append(str(row["criterion_id"]))
        groups.append((declaration, criterion_ids))
    return rows, groups


def structure_trial_criteria(
    *,
    record: TeamTrialRecord,
    group_id: str,
    maximum_criteria: int,
    minimum_criteria: int,
    logic_declarations: Sequence[ExplicitLogicGroup] = (),
) -> tuple[list[dict[str, Any]], CriterionLogic, dict[str, int]]:
    """Return a small executable subset plus an explicit logic tree."""

    candidates = _age_rows(record, group_id)
    pregnancy = _keyword_exclusion_row(
        record,
        group_id,
        fact_code="pregnancy_or_lactation",
        description="현재 임신 또는 수유 여부",
        pattern=_PREGNANCY_RE,
        acquisition_mode="patient_report",
    )
    if pregnancy is not None:
        candidates.append(pregnancy)
    infection = _keyword_exclusion_row(
        record,
        group_id,
        fact_code="active_serious_infection",
        description="현재 중대한 활동성 감염 여부",
        pattern=_INFECTION_RE,
        acquisition_mode="internal_record",
    )
    if infection is not None:
        candidates.append(infection)
    candidates.extend(_numeric_rows(record, group_id))

    logic_rows, declared_groups = _logic_rows(
        record,
        group_id,
        logic_declarations,
    )
    logic_line_numbers = {int(row["line_number"]) for row in logic_rows}
    candidates = [
        row
        for row in candidates
        if int(row["line_number"]) not in logic_line_numbers
        or row["source_field"] != "eligibility_text"
    ]
    ordinary_limit = max(0, maximum_criteria - len(logic_rows))
    has_structured_age = any(row["fact_code"] == "age_years" for row in candidates)
    fallback_candidates = [
        row
        for row in _fallback_predicate_rows(record, group_id)
        if not (
            has_structured_age
            and _EXPLICIT_AGE_LINE_RE.search(str(row["source_text"]))
        )
        and (
            row["source_field"],
            int(row["line_number"]),
        )
        not in {
            (item["source_field"], int(item["line_number"]))
            for item in [*candidates, *logic_rows]
        }
    ]
    reserved_fallback = fallback_candidates[0] if fallback_candidates else None
    candidate_limit = ordinary_limit - (1 if reserved_fallback is not None else 0)
    selected = candidates[: max(0, candidate_limit)]
    if reserved_fallback is not None:
        selected.append(reserved_fallback)
    if len(selected) + len(logic_rows) < minimum_criteria:
        used_lines = {
            (row["source_field"], int(row["line_number"]))
            for row in [*selected, *logic_rows]
        }
        for row in fallback_candidates:
            key = (row["source_field"], int(row["line_number"]))
            if key in used_lines:
                continue
            selected.append(row)
            used_lines.add(key)
            if len(selected) + len(logic_rows) >= minimum_criteria:
                break
    rows = [*selected, *logic_rows]
    if len(rows) < minimum_criteria:
        raise ValueError(
            f"{record.nct_id} yielded only {len(rows)} conservative criteria"
        )
    if len(rows) > maximum_criteria:
        raise ValueError("logic declarations exceed maximum_criteria")

    for sequence, row in enumerate(rows, start=1):
        new_id = f"{record.nct_id}:criterion:{sequence:02d}"
        row["criterion_id"] = new_id
        row["candidate_id"] = f"{record.nct_id}:candidate:{sequence:02d}"

    group_nodes = []
    grouped_line_numbers: set[int] = set()
    for declaration, _ in declared_groups:
        matching_rows = [
            row
            for row in rows
            if row["confidence"] == "declared_explicit_logic"
            and int(row["line_number"]) in declaration.source_line_numbers
        ]
        grouped_line_numbers.update(declaration.source_line_numbers)
        group_nodes.append(
            CriterionLogic(
                operator=CriterionLogicOperator(declaration.operator),
                label=declaration.label,
                minimum_required=declaration.minimum_required,
                children=[
                    CriterionLogic(
                        operator=CriterionLogicOperator.CRITERION,
                        criterion_id=str(row["criterion_id"]),
                    )
                    for row in matching_rows
                ],
            )
        )
    ordinary_nodes = [
        CriterionLogic(
            operator=CriterionLogicOperator.CRITERION,
            criterion_id=str(row["criterion_id"]),
        )
        for row in rows
        if not (
            row["confidence"] == "declared_explicit_logic"
            and int(row["line_number"]) in grouped_line_numbers
        )
    ]
    logic = CriterionLogic(
        operator=CriterionLogicOperator.ALL,
        label="이 평가에 구조화한 조건",
        children=[*ordinary_nodes, *group_nodes],
    )
    counts = Counter(str(row["confidence"]) for row in rows)
    return rows, logic, dict(sorted(counts.items()))


def structure_selected_source_trials(
    *,
    selection: Mapping[str, Any],
    records: Mapping[str, TeamTrialRecord],
    minimum_criteria_per_trial: int,
    maximum_criteria_per_trial: int,
    logic_declarations: Sequence[ExplicitLogicGroup],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, int]]:
    """Structure every selected trial and retain its exact source references."""

    trials = []
    criteria = []
    total_counts: Counter[str] = Counter()
    declarations_by_trial: dict[str, list[ExplicitLogicGroup]] = {}
    for declaration in logic_declarations:
        declarations_by_trial.setdefault(declaration.trial_id, []).append(declaration)

    for item in selection["selected_trials"]:
        trial_id = str(item["nct_id"])
        record = records.get(trial_id)
        if record is None:
            raise ValueError(f"selected trial is absent from source corpus: {trial_id}")
        rows, logic, counts = structure_trial_criteria(
            record=record,
            group_id=str(item["group_id"]),
            maximum_criteria=maximum_criteria_per_trial,
            minimum_criteria=minimum_criteria_per_trial,
            logic_declarations=declarations_by_trial.get(trial_id, ()),
        )
        total_counts.update(counts)
        criteria.extend(rows)
        group = next(
            row for row in selection["groups"] if row["group_id"] == item["group_id"]
        )
        trials.append(
            {
                "group_id": str(item["group_id"]),
                "group_label": str(group["group_label"]),
                "nct_id": trial_id,
                "title": record.title,
                "study_url": _source_url(trial_id),
                "conditions": list(record.conditions),
                "overall_status": record.overall_status,
                "eligibility_logic": logic.model_dump(mode="json"),
                "structured_criterion_count": len(rows),
            }
        )
    return trials, criteria, dict(sorted(total_counts.items()))


__all__ = [
    "AcquisitionModeValue",
    "ExplicitLogicGroup",
    "plausible_numeric_range",
    "structure_selected_source_trials",
    "structure_trial_criteria",
]
