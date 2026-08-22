# Objective clinical-trial criterion review

## Task

Review every supplied source line for a preliminary synthetic evaluation dataset.
You are not diagnosing a patient and you are not deciding whether a real patient
can enroll. Decide only whether the line can be converted into one or more
deterministic facts without adding medical knowledge that is absent from the
source.

## Include a line only when

- it states a numeric threshold, time window, documented test result, diagnosis,
  treatment or medication history, or another explicit patient state;
- a synthetic patient record can state the required fact directly; and
- two careful non-clinical researchers could copy the rule from this source.

An objective score threshold such as `ECOG <= 1` may be included when the patient
record will provide an already documented score. Do not infer the score yourself.

## Exclude or mark uncertain

- `exclude`: headings, list introductions, explanatory fragments, non-restrictive
  statements, and criteria that depend entirely on investigator discretion;
- `uncertain`: nested exceptions, conditional thresholds, ambiguous logical
  structure, specialist interpretation not reducible to an already documented
  result, or a line whose meaning depends on missing context.

Do not force every line into the dataset. A smaller set of clear rules is better.

## Annotations

- Return every supplied `candidate_id` exactly once and in the same order.
- Split a line into multiple annotations when it contains several independently
  testable conditions.
- Use a stable positive `fact_code` in lowercase snake case.
- For a numeric rule, copy the comparison, number, and unit exactly. Use `gt`,
  `gte`, `lt`, `lte`, or `eq`; set `expected_value` to null.
- For a non-numeric explicit state, leave the numeric fields null and use a short
  lowercase snake-case `expected_value`, such as `present`, `absent`, `positive`,
  `negative`, `diagnosed`, `true`, or `false`.
- `expected_value` is the patient value that makes the source line itself true.
  The `section` field later determines whether that truth includes or excludes
  the patient. Do not reverse an exclusion line into the safer patient state.
  For example, exclusion `Pregnant or breastfeeding` becomes `pregnancy =
  present` and `breastfeeding = present`; inclusion `No current insulin use`
  becomes `current_insulin_use = absent`.
- Express negation through a positive fact code plus the expected value. Example:
  `not using narcotics in the last 4 hours` becomes fact code
  `narcotic_use_within_4_hours` with expected value `absent`.
- `expected_value` must be one of `present`, `absent`, `positive`, `negative`,
  `diagnosed`, `not_diagnosed`, `true`, or `false`. If an allowed set or another
  value cannot be represented with these states, use `uncertain`.
- Do not convert units, invent freshness requirements, or add an official test
  requirement that the source does not state.
- Use `include` only at high or medium confidence. Use `uncertain` at low confidence.
- Keep `note` concise. Do not provide hidden reasoning or medical advice.

Return only the required structured output.
