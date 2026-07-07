# Patient Profile Extraction — Implementation Notes

First concrete implementation step for the **Patient Profile
Understanding Agent** of the locked v1.2-final architecture:
natural-language patient summary → structured `PatientProfile` →
Pydantic validation.

## Current implementation: deterministic fallback

`agents/patient_profile_understanding.py::extract_patient_profile_from_summary`
is an offline heuristic, NOT full LLM extraction:

- regex extraction of age ("62-year-old", "3-month-old" → 0) and sex
  (word-boundary matching, female checked before male);
- a small keyword list for obvious diagnoses and a `stage X` regex;
- everything the heuristics cannot find is preserved as `None` /
  `"unknown"` — values are never fabricated;
- the original summary is kept verbatim in `free_text_notes`;
- the return value is always a valid `PatientProfile`, and it requires
  no network access or API keys (enforced by tests that strip key env
  vars).

## Future implementation: LLM structured output

The prompt template at `prompts/patient_profile_extraction.md` defines
the target behavior: schema-constrained JSON matching `PatientProfile`,
evidence sentences quoted verbatim, unknowns kept explicit, no diagnosis
overclaiming, no medical advice. The LLM call will replace the heuristic
body behind the exact same typed signature, so callers and tests of the
contract are unaffected. Each call will be logged via `RequestLog`.

## Architecture fit

This is the input-normalization boundary of the locked architecture: raw
free text enters only here, and only normalized, validated data flows on
to the Eligibility State Tracker and the rule layer. Professor-provided
summaries pass through this same contract as inputs only — never as
eligibility labels.

## What this proves / does not prove

Proves: the summary → profile → validation path runs end to end offline;
unknowns degrade gracefully instead of being guessed; both synthetic and
professor summaries satisfy the input contract; the demo is reproducible
with one command.

Does not prove: real extraction quality (the heuristics only catch
obvious patterns), LLM behavior, or any clinical correctness — synthetic
data only, not medical advice.
