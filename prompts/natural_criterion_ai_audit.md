# Audit an objective clinical-trial criterion draft

## Task

Independently recheck every supplied source line and its first-pass draft. Return
a complete corrected review. Use the source, not the draft, as authority.

The result is a single-model preliminary research annotation, not clinical gold.
Be conservative. Reject convenient-looking labels when the source is a heading,
depends on investigator judgment, contains unsupported conditional logic, or
would require you to diagnose or infer a clinical score. A documented score,
diagnosis, pathology result, medication history, or patient-reported state can be
used when the synthetic record will state it directly.

Apply the same annotation rules as the first pass:

- return every `candidate_id` once and in source order;
- split independently testable conditions;
- use positive snake-case fact codes;
- numeric rules require an exact operator, threshold, and unit and have a null
  `expected_value`;
- explicit non-numeric states require an `expected_value` and null numeric fields;
- `expected_value` is the patient value that makes the source line itself true;
  do not reverse an exclusion criterion into the safer eligibility state. Thus
  exclusion `Pregnant` means `pregnancy = present`, while inclusion `No current
  insulin use` means `current_insulin_use = absent`;
- `expected_value` is limited to `present`, `absent`, `positive`, `negative`,
  `diagnosed`, `not_diagnosed`, `true`, or `false`; otherwise use `uncertain`;
- do not convert units or invent facts;
- use `uncertain` instead of guessing.

Keep notes concise and return only the required structured output.
