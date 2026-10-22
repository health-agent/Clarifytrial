"""Keep unrepresented protocol text visible beside an executable subset.

Lines are traceable source units, not adjudicated clinical criteria. This report
cannot turn a numeric-subset confirmation into full clinical eligibility.
"""
from __future__ import annotations


def describe_protocol_scope(trial: dict) -> dict:
    lines=trial["eligibility_text"].splitlines()
    represented=set()
    for row in trial["criteria"]:
        numbers=row.get("source_line_numbers",[int(row["line_number"])])
        if any(not 1 <= number <= len(lines) for number in numbers) or "\n".join(lines[number-1].strip() for number in numbers)!=row["source_text"].strip():
            raise ValueError(f"criterion source span does not match {trial['nct_id']} lines {numbers}")
        represented.update(numbers)
    unrepresented=[{"line_number":i,"source_text":line.strip()} for i,line in enumerate(lines,1)
                   if line.strip() and i not in represented]
    return {"assessment_scope":"selected_numeric_and_declared_boolean_conditions_only" if any(row["unit"]=="bool" for row in trial["criteria"]) else "selected_numeric_conditions_only",
            "full_protocol_confirmation_allowed":False,
            "full_protocol_status":"requires_review_of_unrepresented_conditions",
            "represented_source_line_count":len(represented),
            "unrepresented_source_line_count":len(unrepresented),
            "unrepresented_source_line_numbers":[row["line_number"] for row in unrepresented],
            "partially_represented_source_line_numbers":sorted(represented),
            "fully_represented_source_line_numbers":[],
            "counting_note":"Source lines include headings and prose, not total clinical criterion counts. Selected numeric/boolean spans do not establish that all semantics on those lines are represented."}
