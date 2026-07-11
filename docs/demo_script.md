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
> layer, several deterministic agent fallbacks, and 102 passing tests
> across four synthetic datasets. The live LLM, RAG, and orchestration
> runtime remain future work, so the decision logic stays verifiable
> while each language component is added."

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
   Then run `python -m pytest -q` live: 102 tests pass in about a second.
4. **The data harness** (30s): `examples/` has a full valid demo session,
   3 mock trial protocols, 6 labeled matching scenarios covering all four
   recommendation labels, and two patient-summary input sets. Run
   `python scripts/validate_synthetic_data.py` to show the validation
   summary.
5. **What's next** (15s): provenance-tracked ClinicalTrials.gov, TrialGPT
   and TREC adapters; then the three baseline modes and masked interactive
   benchmark; Solar and LangGraph are added only after that offline harness.

## What to show first in the repo

1. `docs/architecture.md` — the system in one diagram.
2. `models.py` — the locked invariants are in the module docstring and
   enforced by the schema (e.g. `clarification_round_count` capped at 3).
3. `rules.py` — the decision logic, small enough to read in full.
4. A live `python -m pytest -q` run — 102 passed.

## Which files prove the architecture

| Claim | File |
|---|---|
| Central shared session state, multi-trial | `models.py` (`PatientSession`, `TrialState`) |
| Deterministic decision layer | `rules.py` |
| 10-agent decomposition with typed contracts | `agents/` (docstrings state each responsibility) |
| The whole schema composes into a real session | `examples/demo_patient_session.json` + `tests/test_models_validate.py` |
| Rule semantics verified | `tests/test_effect_rules.py`, `tests/test_recommendation_rules.py`, `tests/test_missing_variable_dedup.py` |

## How to explain the pytest results

"102 tests, all passing. They cover the locked effect and recommendation
rules, shared-state schemas, global missing-variable deduplication,
heuristic criteria parsing, patient extraction, criterion matching,
question construction, first-step answer normalization, synthetic datasets
and the deterministic end-to-end demo. They prove software contracts and
rule behavior, not clinical accuracy or real LLM quality."

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
