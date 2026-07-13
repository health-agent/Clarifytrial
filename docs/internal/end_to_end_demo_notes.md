# End-to-End Dry-Run Demo — Notes

`scripts/run_end_to_end_demo.py` runs the full state flow of the locked
v1.2-final architecture deterministically, on synthetic data only, with
no LLM calls, no external APIs, and no API keys.

## What the dry run demonstrates

The complete pipeline through the real models and rules:

synthetic patient summary → `PatientSession` (central shared state) →
per-trial `criterion_states` under `trial_states_by_trial_id` →
`rules.derive_eligibility_effect` (including a conflict routed to
review) → `rules.deduplicate_missing_variables` (the same
`ecog_performance_status` key from two trials becomes ONE pool item) →
global clarification queue (`FollowUpQuestion`, round 1) →
`rules.compute_trial_recommendation` + `rules.rank_trials` (a blocked
trial ranks last regardless of relevance) → `FinalOutput` → a markdown
report at `outputs/end_to_end_demo_summary.md`.

## What stands in for LLM output

Only the criterion match statuses (met / unmet / unknown / conflict) are
mocked — they are hard-coded stand-ins for what the LLM Criterion
Matching Agent would produce, and the patient profile comes from the
small deterministic heuristic extractor. Everything downstream of those
statuses is the real rule code, unmodified.

## What it proves

- The locked architecture composes end to end: models, rules, and state
  structures fit together without gaps.
- Global missing-variable dedup, the global question queue, review
  escalation on conflict, and recommendation precedence/ranking all work
  on a realistic multi-trial session.
- The pipeline is fully offline and reproducible (verified by
  `tests/test_end_to_end_demo.py`, which strips API-key env vars).

## What it does NOT prove

- No clinical-grade language understanding: match statuses are mocked and
  the profile extractor handles only a small set of explicit patterns.
- No clinical accuracy of any kind — synthetic dry run, not medical
  advice.
- No interactive loop yet: the clarification round is staged, not driven
  by real answers (that is Week-5 roadmap work).
