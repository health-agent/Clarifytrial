# Criteria Parser — Implementation Notes

Second concrete agent implementation step (mirroring the patient profile
extraction layer): trial protocol text → structured `Criterion` objects
+ `TrialContext` → Pydantic validation. This is the **Criteria Parser
Agent** of the locked v1.2-final architecture.

## Current implementation: deterministic fallback

`agents/criteria_parser.py::parse_trial_criteria_from_text` is an
offline heuristic, NOT full LLM extraction:

- section headers ("Inclusion Criteria:" / "Exclusion Criteria:") split
  the text; bullet/numbered/plain lines under each header become one
  criterion each;
- stable sequential ids: `{trial_id}-INC-01`, `{trial_id}-EXC-01`, ...;
- `criterion_type` comes from the section, never guessed;
- `required_variables` come from a small keyword map onto canonical
  snake_case keys (age, sex, diagnosis, disease_stage, biomarker_status,
  prior_treatment, current_treatment, ecog_performance_status,
  renal_function, hba1c, pregnancy_status, comorbidities);
- sparse input degrades gracefully: a missing exclusion section yields
  zero exclusion criteria; empty text yields no criteria at all;
- everything returned is Pydantic-validated; no network, no API keys
  (test-enforced). `parse_criteria(protocol)` is now a thin wrapper over
  the same function using `eligibility_criteria_raw`.

## Future implementation: LLM structured output

`prompts/criteria_parsing.md` defines the target: schema-constrained
JSON with normalized criterion meanings and explicit
thresholds/time-windows/biomarkers/lab-values where present, unknowns
kept explicit. It will replace the heuristic behind the same signature,
logged via `RequestLog`.

## Locked invariants honored

- **Trial description is context/relevance support only.** Text outside
  the inclusion/exclusion sections goes into `TrialContext.description`
  and never becomes a criterion — the parser structurally cannot invent
  blocking criteria from the description (tested).
- **Inclusion/exclusion semantics are preserved exactly.** This matters
  because the rule mappings invert per type: `met` supports eligibility
  for an inclusion criterion but blocks it for an exclusion criterion.
  A parser that misclassifies the section flips the eligibility effect,
  so the type comes only from explicit headers.
- **Canonical variable keys.** Consistent snake_case keys are required
  for the global missing-variable deduplication to work across trials.

## What this proves / does not prove

Proves: raw trial text → typed criteria → validation runs end to end
offline; section typing, stable ids, and variable keys behave
deterministically; the parsed output plugs directly into the existing
models used by the rules and demos.

Does not prove: real parsing quality on messy real-world protocol text
(the heuristics assume clean headers and line-per-criterion structure),
LLM behavior, or any clinical correctness — synthetic data only, not
medical advice.
