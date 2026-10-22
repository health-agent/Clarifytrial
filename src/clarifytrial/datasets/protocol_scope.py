"""Keep unrepresented protocol text visible beside an executable subset.

Lines are traceable source units, not adjudicated clinical criteria. This report
cannot turn a numeric-subset confirmation into full clinical eligibility.
"""
from __future__ import annotations


def describe_protocol_scope(trial: dict) -> dict:
    lines=trial["eligibility_text"].splitlines()
    represented=set()
    for row in trial["criteria"]:
        number=int(row["line_number"])
        if not 1 <= number <= len(lines) or lines[number-1].strip()!=row["source_text"].strip():
            raise ValueError(f"criterion source span does not match {trial['nct_id']} line {number}")
        represented.add(number)
    unrepresented=[{"line_number":i,"source_text":line.strip()} for i,line in enumerate(lines,1)
                   if line.strip() and i not in represented]
    return {"assessment_scope":"selected_numeric_conditions_only",
            "full_protocol_confirmation_allowed":False,
            "full_protocol_status":"requires_review_of_unrepresented_conditions",
            "represented_source_line_count":len(represented),
            "unrepresented_source_line_count":len(unrepresented),
            "unrepresented_source_line_numbers":[row["line_number"] for row in unrepresented],
            "counting_note":"Source lines include headings and explanatory prose; these are not total clinical criterion counts."}
