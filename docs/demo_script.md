# Demo Script — Presenting ClarifyTrial Agent

## 60-second version

> "ClarifyTrial Agent is a multi-agent system for interactive clinical
> trial recommendation. The key design decision: LLM agents only handle
> language — parsing criteria, extracting patient variables, phrasing
> questions. Eligibility decisions come from locked, deterministic rules
> over a central shared state that tracks every criterion of every trial
> as met, unmet, unknown, conflicting, or not applicable. Unknowns become
> deduplicated clarification questions — one global queue, max three
> rounds — and conflicting evidence escalates to human review instead of
> being guessed away. What exists today is the complete schema and rule
> layer with 40 passing tests: every rule mapping, the full
> recommendation precedence, and four synthetic datasets, including a
> professor-provided patient input set. The LLM internals are typed stubs
> — deliberately, so the decision logic was verifiable first."

## 3-minute version

Add, in this order:

1. **The problem** (30s): trial matching is interactive — summaries never
   contain every required variable; the same missing variable (e.g. ECOG
   status) blocks criteria across many trials; evidence can conflict. A
   one-shot chatbot answer hides all of that.
2. **The architecture** (60s): open `docs/architecture.md` (Mermaid
   flowchart). Walk the loop: criteria parsing and profile extraction
   feed the Eligibility State Tracker; unknown criteria feed the global
   missing-variable pool (deduplicated by `missing_variable_key`); the
   pool feeds one global clarification queue; answers are normalized
   before rules run; only affected criteria are re-evaluated. Then open
   `docs/state_transition.md` for the criterion state machine and the
   two review paths (conflict, max-3-rounds).
3. **The guarantees** (45s): open `rules.py` — four pure functions, no
   side effects. Point at the precedence: a blocking criterion always
   wins; review beats uncertainty; relevance score affects ranking only.
   Then run `python -m pytest -q` live: 40 tests pass in under a second.
4. **The data harness** (30s): `examples/` has a full valid demo session,
   3 mock trial protocols, 6 labeled matching scenarios covering all four
   recommendation labels, and two patient-summary input sets. Run
   `python scripts/validate_synthetic_data.py` to show the validation
   summary.
5. **What's next** (15s): state-tracker mutations, then a deterministic
   mock matcher to replay the labeled scenarios end to end, then LLM
   internals behind the same typed contracts.

## What to show first in the repo

1. `docs/architecture.md` — the system in one diagram.
2. `models.py` — the locked invariants are in the module docstring and
   enforced by the schema (e.g. `clarification_round_count` capped at 3).
3. `rules.py` — the decision logic, small enough to read in full.
4. A live `python -m pytest -q` run — 40 passed.

## Which files prove the architecture

| Claim | File |
|---|---|
| Central shared session state, multi-trial | `models.py` (`PatientSession`, `TrialState`) |
| Deterministic decision layer | `rules.py` |
| 10-agent decomposition with typed contracts | `agents/` (docstrings state each responsibility) |
| The whole schema composes into a real session | `examples/demo_patient_session.json` + `tests/test_models_validate.py` |
| Rule semantics verified | `tests/test_effect_rules.py`, `tests/test_recommendation_rules.py`, `tests/test_missing_variable_dedup.py` |

## How to explain the pytest results

"40 tests, all passing, in four groups: (1) every locked effect mapping,
including conflict → review and unknown-after-3-rounds → review; (2) the
recommendation precedence — including that a relevance score of 1.0
cannot rescue a blocked trial and blocked trials always rank last; (3)
global missing-variable deduplication across trials; (4) schema and
input-contract validation of all five example datasets. They prove the
decision layer and schemas are correct. They do NOT claim clinical
accuracy or LLM extraction quality — those layers are stubs by design."

## How to explain the professor patient summary dataset — correctly

`examples/professor_patient_summaries.json` is a professor-provided set
of 10 natural-language case vignettes. Say exactly this:

- "These are **input examples**: they validate that our Patient Profile
  Understanding contract accepts realistic, varied clinical narratives —
  ages from infant to elderly, many specialties."
- "They are **not ground truth**: they carry no eligibility labels, no
  expected recommendations, and no test treats them as such. Expected
  labels live only in the separate, explicitly synthetic
  `synthetic_matching_scenarios.json`."
- The file is kept read-only and separate from the project-generated
  `synthetic_patients.json`; see `docs/professor_patient_input_notes.md`.

Never claim the system "diagnoses" or "matches" these vignettes — today
it only proves it can ingest them through a typed, testable contract.
