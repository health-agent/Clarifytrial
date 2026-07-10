# ClarifyTrial Agent v1.2-final — Project Status Report

_Last updated: 2026-07-11. Grounded in the current repository contents;
every claim below can be checked against a file in this repo (see
`docs/evidence_table.md`)._

## Project summary

ClarifyTrial Agent is a shared-state multi-agent system for Interactive
Clinical Trial Recommendation: eligibility criteria from multiple trials
are evaluated against a patient profile, unknowns are turned into
deduplicated clarification questions asked in a bounded loop (max 3
rounds), and per-trial recommendations are produced by locked,
deterministic precedence rules rather than by LLM judgment. The current
repository is the v1.2-final skeleton and validation harness: the complete
data schema layer (Pydantic v2), the complete pure-rule layer, mixed-maturity
agent contracts and deterministic heuristic demos, four synthetic/mock
datasets, architecture docs and a pytest suite of 102 passing tests. No LLM calls, no external
APIs, no real patient data, and no clinical decision support.

## Current architecture status

The v1.2-final architecture is locked and encoded in code and docs:

- Central shared state: `models.PatientSession`, session-level, keyed by
  `patient_id`, owned by the Eligibility State Tracker
  (`agents/eligibility_state_tracker.py`).
- Multi-trial state: `PatientSession.trial_states_by_trial_id`, each
  `TrialState` holding its own `trial_context` and `criterion_states`.
- Global missing-variable pool deduplicated by `missing_variable_key`
  (`rules.deduplicate_missing_variables`).
- Single global clarification queue (`global_clarification_queue`),
  never per trial; `clarification_round_count` capped at 3 by the schema
  itself (`le=3`).
- Deterministic rule layer (`rules.py`): effect mapping, dedup,
  recommendation precedence, ranking. `trial_relevance_score` feeds
  `ranking_score` only and can never override a hard block.
- Diagrams: `docs/architecture.md` (flowchart) and
  `docs/state_transition.md` (criterion state machine).

## Implemented artifacts

| Layer | Status |
|---|---|
| `models.py` — 21 Pydantic v2 models + 7 locked enums | Implemented |
| `rules.py` — 4 pure rule functions | Implemented and fully tested |
| `agents/` — 10 agent modules | Contracts for the full workflow; heuristic criteria parsing, patient extraction, matching, missing-info detection, question building and first-step answer normalization are runnable; state mutation, real LLM calls and final explanation remain incomplete |
| `scripts/` — deterministic demos and dataset validation | Implemented, runnable from the repository root |
| `tests/` — 14 files, 102 tests | All passing on Python 3.13; CI covers Python 3.10 and 3.13 |
| Docs and visual workflow | Architecture, state transitions, easy Korean overview and implementation direction are written |

## Dataset files included (all synthetic/mock)

- `examples/demo_patient_session.json` — one full `PatientSession`: two
  mock trials, a cross-trial deduplicated missing variable, one queued
  question, two recommendations.
- `examples/synthetic_patients.json` — 10 project-generated case
  summaries (S001–S010), input examples only.
- `examples/synthetic_trial_protocols.json` — 3 mock `TrialProtocol`
  records with criteria text and static source metadata.
- `examples/synthetic_matching_scenarios.json` — 6 labeled scenarios
  covering all four recommendation labels.
- `examples/professor_patient_summaries.json` — professor-provided,
  read-only, 10 case summaries; a separate input robustness set.

## Professor patient summary input validation

`tests/test_professor_patient_summaries_load.py` (7 tests) verifies the
professor dataset validates against `SyntheticPatientDataset`, contains
exactly 10 cases with "S"-prefixed nums and non-empty titles, and that
every title passes the input contract of
`extract_patient_profile_from_summary`, returning a valid
`PatientProfile`. These summaries are natural-language INPUTS only — never
eligibility or recommendation ground truth (see
`docs/professor_patient_input_notes.md`).

## Test coverage summary (102 tests, all passing)

| File | Tests | Covers |
|---|---|---|
| `test_effect_rules.py` | 10 | All locked effect mappings incl. conflict and max-rounds review |
| `test_recommendation_rules.py` | 6 | All four precedence outcomes; relevance never overrides a block; ranking |
| `test_missing_variable_dedup.py` | 2 | Global dedup across trials and within a trial |
| `test_models_validate.py` | 2 | Demo session validates; locked invariants hold in the example |
| `test_synthetic_patients_load.py` | 4 | Dataset schema + extraction input contract |
| `test_synthetic_trial_protocols_load.py` | 4 | Protocol schema, criteria present, static sources |
| `test_synthetic_matching_scenarios.py` | 5 | Scenario schema, full label coverage, locked enum types |
| `test_professor_patient_summaries_load.py` | 7 | Professor dataset shape + input contract |
| `test_patient_profile_extraction.py` | 6 | Deterministic age/sex extraction boundaries |
| `test_criteria_parser.py` | 9 | Criterion splitting, IDs and required-variable hints |
| `test_criterion_matching.py` | 10 | Heuristic met/unmet/unknown/conflict matching behavior |
| `test_missing_info_clarification.py` | 9 | Global pool priorities, deduplication and question construction |
| `test_answer_update_step1.py` | 23 | Answer normalization and guarded patient-profile updates |
| `test_end_to_end_demo.py` | 5 | Offline demo contract, recommendations, questions and disclaimer |

## What the passing tests prove

- The locked rule semantics are implemented exactly: every effect
  mapping, the conflict and max-rounds review paths, and the four-step
  recommendation precedence behave as specified.
- A relevance score of 1.0 cannot rescue a hard-blocked trial, and
  blocked trials always rank below non-blocked ones.
- Missing variables deduplicate globally across trials by
  `missing_variable_key`.
- The schema layer is coherent: a realistic full-session example
  validates end to end, and all four datasets conform to their models.
- The Patient Profile Understanding Agent's input contract accepts all
  20 natural-language summaries (10 synthetic + 10 professor-provided).

## What the tests do NOT prove

- No clinical correctness of any kind — all data is synthetic/mock.
- No clinical extraction quality: the current deterministic implementation
  extracts only a small set of explicitly written fields and remains a demo.
- No end-to-end matching accuracy: the labeled scenarios validate schema
  and label coverage, not a running pipeline that reproduces the labels.
- Nothing about real ClinicalTrials.gov data or any live source.

## Remaining TODOs

- Replace current heuristics with versioned LLM implementations for criteria
  parsing, profile extraction, evidence retrieval, criterion matching,
  patient-friendly question phrasing and explanations.
- Eligibility State Tracker mutations (trial registration, status
  updates, round increments) — currently stubs raising
  `NotImplementedError`.
- A LangGraph orchestration loop with persistence, question interrupts and
  targeted re-evaluation.
- ClinicalTrials.gov API v2 ingestion adapter (fields already exist on
  `TrialProtocol`); Synthea-style richer synthetic patients (documented
  future work).

## Safest next implementation steps

1. Implement the Eligibility State Tracker mutations with pure Python and keep
   the tested decision rules unchanged.
2. Wrap the existing agent interfaces as LangGraph nodes and pause at the
   patient-answer boundary.
3. Add ClinicalTrials.gov ingestion and independently measured RAG retrieval.
4. Replace heuristics with real LLM calls one agent at a time, preserving
   deterministic baselines and contract tests.
5. Evaluate question value with an independent hidden-state patient simulator.
