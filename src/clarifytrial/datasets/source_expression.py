"""Literal guard for author-declared source expressions, not a clinical NLP oracle.

This checks printed sign/value support and declared count conversions. It cannot
establish that a comparator belongs to the right concept, population, or phase.
Those source meanings require separately reviewed declarations.
"""
from __future__ import annotations

import re
from .source_criteria import _normalize_comparators


def validate_declared_numeric_expression(expression: dict, quote: str) -> None:
    if "fact_code" not in expression:
        if expression.get("operator") not in {"all","any"} or not expression.get("children"):
            raise ValueError("source expression must be a nonempty AND/OR or scalar")
        for child in expression["children"]:
            validate_declared_numeric_expression(child,quote)
        return
    op=expression["operator"]
    if expression["unit"]=="bool":
        vocabulary={"liver_metastases":"liver metastases","gilbert_syndrome":"gilbert","dialysis":"dialysis"}
        if expression["fact_code"] not in vocabulary or op!="eq" or expression["threshold"] not in {0,1}:
            raise ValueError("boolean source meaning needs an explicit reviewed vocabulary")
        if vocabulary[expression["fact_code"]] not in quote.casefold():
            raise ValueError("declared boolean concept absent from source")
        return
    if op=="eq":
        if (expression["fact_code"]!="ecog_score" or expression["threshold"] not in {0,1}
            or not re.search(r"\b0\s+or\s+1\b",quote)):
            raise ValueError("unwritten equality/set constraint")
        return
    normalized=_normalize_comparators(quote)
    value=expression.get("source_threshold",expression["threshold"])
    if "source_threshold" in expression:
        if (expression.get("source_unit") not in {"/mm3","/uL"} or expression["unit"]!="10^9/L"
            or abs(value/1000-expression["threshold"])>1e-9):
            raise ValueError("unsupported count-unit conversion")
    symbol={"gte":">=","lte":"<=","gt":">","lt":"<"}[op]
    comparisons=[(m.group(1),float(m.group(2).replace(",",""))) for m in re.finditer(r"(>=|<=|>|<)\s*(\d+(?:,\d{3})*(?:\.\d+)?)",normalized)]
    if (symbol,value) in comparisons:
        return
    between=re.search(r"between\s+(\d+(?:\.\d+)?)\s+and\s+(\d+(?:\.\d+)?)",normalized,re.I)
    if between and ((op=="gte" and value==float(between[1])) or (op=="lte" and value==float(between[2]))):
        return
    raise ValueError(f"declared {symbol}{value:g} is not supported by the quoted numeric expression")
