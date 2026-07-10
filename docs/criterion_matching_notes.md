# Criterion Matching — Implementation Notes

Third concrete agent implementation step (mirroring the profile
extraction and criteria parser layers): `PatientProfile` + parsed
`Criterion` → `CriterionState` with match status, derived eligibility
effect, evidence, and missing variables. This is the **Criterion
Matching & Reasoning Agent** of the locked v1.2-final architecture.

## Current implementation: deterministic conservative fallback

`agents/criterion_matching.py::match_criterion_against_patient` is an
offline heuristic, NOT a clinical reasoner:

- explicit age thresholds/ranges in the criterion text are compared
  against a known age → met/unmet;
- a known diagnosis clearly appearing in the criterion text → met;
- a single known boolean-like variable → met (truthy) / unmet (falsy);
- a variable explicitly recorded as "conflict" → conflict;
- anything else — above all, ANY missing required variable — → unknown,
  with canonical `missing_variable_keys`;
- simple evidence text from the profile fields used is attached as an
  `EvidenceContext`;
- no network, no API keys (test-enforced).

## Why match status is separate from inclusion/exclusion semantics

The agent answers one factual question: "does the criterion's condition
hold for this patient?" — `met` on an exclusion criterion means the
patient HAS the excluded condition. Whether that supports or blocks
eligibility is the locked rule mapping's job
(`rules.derive_eligibility_effect`), which inverts per criterion type.
The matcher calls that rule and never reimplements it; a test asserts
the returned effect always equals the rule output. This separation is
what keeps eligibility decisions deterministic and auditable even after
the matcher becomes an LLM.

## Why missing information is `unknown`, never negative evidence

Inferring absence from silence would silently convert "the summary
didn't mention prior chemotherapy" into "the patient had no prior
chemotherapy" — an unsafe fabrication. The fallback therefore returns
`unknown` plus a `missing_variable_key`, which is exactly the signal the
downstream loop needs.

## Connection to the rest of the architecture

The emitted `CriterionState`s are what the Eligibility State Tracker
stores under each trial's state. Their `missing_variable_keys` feed
`rules.deduplicate_missing_variables` (Missing Information Detection),
whose pool items become `FollowUpQuestion`s in the global clarification
queue; answers then trigger targeted re-evaluation of exactly these
criteria. The demo script chains profile extraction → parsing → matching
→ dedup to show that path running.

## Future implementation: LLM structured output

`prompts/criterion_matching.md` defines the target: the five-value
status vocabulary only (review routing stays derived, never a status),
evidence summaries with verbatim sentences, confidence, and
missing-variable naming on the canonical keys. It will replace the
heuristic behind the same signature, logged via `RequestLog`.

## What this proves / does not prove

Proves: the profile → criteria → per-criterion state path runs end to
end offline; missing data degrades to `unknown` + clarification signals
instead of guesses; exclusion inversion, conflict-to-review routing, and
effect derivation all flow through the locked rules unchanged.

Does not prove: real matching quality on nuanced criteria (thresholds
beyond age, lab comparisons, temporal logic are out of the heuristic's
scope), LLM behavior, or any clinical correctness — synthetic data only,
not medical advice.
