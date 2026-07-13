# Missing Information Detection + Clarification Queue — Notes

Fourth concrete implementation step: `CriterionState` match results →
**global missing-variable pool** → **global clarification queue**. This
covers the Missing Information Detection Module and the Clarification
Question Agent of the locked v1.2-final architecture. Answer handling is
deliberately NOT included (see "prepares for" below).

## Current implementation: deterministic, offline

- `agents/missing_information_detection.py::build_global_missing_variable_pool`
  collects only states with `criterion_match_status == unknown` and
  non-empty `missing_variable_keys` (unknown without keys is skipped
  gracefully; met/unmet/conflict states never contribute). Dedup and
  trial/criterion traceability reuse the existing pure rule
  `rules.deduplicate_missing_variables` — not reimplemented. Each pool
  item gets an evidence-derived description and a deterministic priority:
  **high** (multiple criteria or trials), **medium** (common screening
  variables: ECOG, renal function, biomarkers, prior treatment),
  **low** (otherwise).
- `agents/clarification_question.py::build_global_clarification_queue`
  creates exactly ONE `FollowUpQuestion` per variable with stable ids
  (Q-001, Q-002, ... in priority-then-key order), template-based plain-
  language wording for common variables, a generic fallback otherwise,
  and full `affected_criterion_ids` traceability. There is no fixed round
  limit by default. Experiments may provide `max_rounds`; when that optional
  limit is exceeded, no new questions are created.
- LLM question phrasing will later use
  `prompts/clarification_question.md` behind the same contract.

## Why pooling is session-level, and why dedup matters

The same variable (e.g. ECOG performance status) is typically required
by criteria in many trials at once. Per-trial questioning would ask the
patient the same thing repeatedly — the locked architecture therefore
pools missing variables at the **patient-session level**, keyed by
`missing_variable_key`, and asks once. One answer then serves every
affected criterion across all trials. This is also why canonical
variable keys matter throughout the pipeline: dedup is exact-key-based.

## Traceability

Every pool item keeps `affected_criterion_ids` and `affected_trial_ids`,
and every question carries the criterion ids forward. Nothing about the
question loses its link back to why it is being asked — which is exactly
what targeted re-evaluation needs next.

## Unknown is never negative evidence

An unanswered variable keeps its criteria `uncertain`; it never becomes
`unmet`. This layer only converts uncertainty into askable questions; it
decides nothing about eligibility.

## How this prepares for Answer Update & Targeted Re-evaluation

The next layer consumes exactly what this one produces: an answered
`FollowUpQuestion` (with its `missing_variable_key` and
`affected_criterion_ids`) will be normalized by the Patient Profile
Understanding Agent into an `AnswerUpdate`, written into the profile,
and then ONLY the affected criteria are re-matched — the traceability
preserved here is the re-evaluation index.

## What this proves / does not prove

Proves: unknowns flow into a deduplicated, prioritized, traceable pool;
one global question per variable with stable ids and bounded rounds; the
whole path runs offline on synthetic data, chained end to end with the
previous three layers.

Does not prove: question-wording quality for uncommon variables (LLM
work), answer handling or re-evaluation (next layer), or any clinical
correctness — synthetic data only, not medical advice.
