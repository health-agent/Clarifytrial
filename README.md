# ClarifyTrial Agent v1.2-final — Skeleton

A minimal, self-verifying Python skeleton for **ClarifyTrial Agent**, a
shared-state multi-agent system for **Interactive Clinical Trial
Recommendation**. This repository contains the locked v1.2-final data
schemas, pure rule functions, agent stubs, docs, a synthetic demo session,
and a pytest validation harness.

> All examples are synthetic/mock. No real patient data, credentials, or
> private institutional information. This is a software skeleton and
> validation harness — **not medical advice**.

## Architecture summary

- The **Eligibility State Tracker** owns the central shared state:
  `PatientSession`, which is **session-level and keyed by `patient_id`**.
- Multiple trials are stored under **`trial_states_by_trial_id`**; each
  trial has its own `trial_context` and `criterion_states`.
- Missing variables are **deduplicated globally by `missing_variable_key`**
  in `global_missing_variable_pool`.
- Clarification questions live in one **global clarification queue**, never
  per trial. `clarification_round_count` is session-level, **max 3**.
- **`trial_relevance_score` affects ranking only, never hard eligibility.**
- Free-text clarification answers are **normalized by the Patient Profile
  Understanding Agent before any rule update**.
- Trial descriptions support context/relevance but **must not create new
  blocking eligibility criteria** unless explicitly stated in the protocol.
- Eligibility effects and recommendations are decided by **pure rules**
  (`rules.py`), never directly by LLM agents:
  - effect mapping (inclusion/exclusion × met/unmet/unknown/conflict/
    not_applicable), and
  - recommendation precedence, applied exactly in order:
    1. any `blocks_eligibility` → `likely_ineligible`
    2. else any `review_required` → `needs_human_review`
    3. else uncertainty ratio above threshold → `uncertain`
    4. else → `likely_eligible`

See `docs/architecture.md` (Mermaid flowchart) and
`docs/state_transition.md` (Mermaid state diagram) for details.

Presentation & proposal materials:

- `docs/project_status_report.md` — current status, test coverage, what
  is and is not proven, safest next steps
- `docs/proposal_brief.md` — proposal-ready project description
- `docs/demo_script.md` — 60-second and 3-minute presentation scripts
- `docs/evidence_table.md` — claims mapped to repo evidence

## File structure

```
clarify_trial_agent/
├── README.md                  # this file
├── requirements.txt           # pydantic>=2.0, pytest>=8.0
├── conftest.py                # sys.path setup so tests import the project
├── models.py                  # Pydantic v2 schemas (locked v1.2-final)
├── rules.py                   # pure rule functions (no side effects)
├── agents/                    # 10 LLM-agent stubs (no real LLM calls)
│   ├── criteria_parser.py
│   ├── patient_profile_understanding.py   # also normalizes free-text answers
│   ├── eligibility_state_tracker.py       # central shared state owner
│   ├── evidence_context_builder.py
│   ├── criterion_matching.py
│   ├── missing_information_detection.py   # global dedup by missing_variable_key
│   ├── clarification_question.py          # global clarification queue
│   ├── answer_update_reevaluation.py      # targeted re-evaluation
│   ├── trial_recommendation.py
│   └── result_explanation.py
├── examples/
│   ├── demo_patient_session.json          # synthetic session, model-valid
│   ├── synthetic_patients.json            # 10 professor-style case summaries (inputs only)
│   ├── synthetic_trial_protocols.json     # 3 mock TrialProtocol records
│   └── synthetic_matching_scenarios.json  # 6 labeled rule-validation scenarios
├── scripts/
│   └── validate_synthetic_data.py         # validates datasets, writes outputs/ summary
├── docs/
│   ├── architecture.md
│   ├── state_transition.md
│   └── synthetic_data_strategy.md
└── tests/
    ├── test_effect_rules.py
    ├── test_recommendation_rules.py
    ├── test_missing_variable_dedup.py
    ├── test_models_validate.py
    ├── test_synthetic_patients_load.py
    ├── test_synthetic_trial_protocols_load.py
    └── test_synthetic_matching_scenarios.py
```

## Mapping to locked v1.2-final invariants

| Invariant | Where it lives |
|---|---|
| Central shared state, session-level, keyed by patient_id | `models.PatientSession`, `agents/eligibility_state_tracker.py` |
| Multiple trials under `trial_states_by_trial_id` | `models.PatientSession.trial_states_by_trial_id` |
| Per-trial `trial_context` + `criterion_states` | `models.TrialState` |
| Global dedup by `missing_variable_key` | `rules.deduplicate_missing_variables`, `models.GlobalMissingVariablePoolItem` |
| Global clarification queue (not per trial) | `models.PatientSession.global_clarification_queue` |
| `clarification_round_count` max 3, session-level | `models.PatientSession` (`le=3`), `models.MAX_CLARIFICATION_ROUNDS` |
| Relevance score affects ranking only | `rules.compute_trial_recommendation` / `rules.rank_trials` |
| Answers normalized before rule update | `agents/patient_profile_understanding.py` |
| Trial description never adds blocking criteria | `models.TrialContext` docstring, `agents/evidence_context_builder.py` |
| Effect mappings & recommendation precedence | `rules.derive_eligibility_effect`, `rules.compute_trial_recommendation` |

## Intentionally not implemented yet

- **Real LLM calls** — all agent functions are typed stubs with TODOs.
- **External API calls** — nothing in this skeleton touches the network.
- **ClinicalTrials.gov adapter** — `models.TrialProtocol` is
  source-agnostic but already carries the fields needed for a planned
  future **ClinicalTrials.gov API v2 ingestion adapter** (`nct_id`,
  `eligibility_criteria_raw`, `conditions`, `interventions`, `source`,
  `source_url`, `retrieved_at`). No API response paths are assumed.
- Orchestration/runtime loop, persistence, and any UI/web app.

## Synthetic data validation harness

Three synthetic datasets live in `examples/` (see
`docs/synthetic_data_strategy.md` for the rationale):

- `synthetic_patients.json` — 10 professor-style synthetic patient case
  summaries (`{"topics": [{"num": "S001", "title": "..."}]}`). These are
  natural-language patient **inputs only**, validating the input contract
  of the Patient Profile Understanding Agent
  (`extract_patient_profile_from_summary`). They are **not** eligibility
  ground truth.
- `synthetic_trial_protocols.json` — 3 mock, source-agnostic
  `TrialProtocol` records with inclusion/exclusion criteria text and
  static source metadata (no live API calls).
- `synthetic_matching_scenarios.json` — 6 labeled scenarios covering all
  four recommendation labels, with expected missing variables and blocking
  criteria. These validate the locked rule semantics, not clinical truth.
- `professor_patient_summaries.json` — a professor-provided, read-only
  input robustness dataset (10 case summaries, same shape as
  `synthetic_patients.json` but kept separate). Input examples only —
  never eligibility or recommendation ground truth. See
  `docs/professor_patient_input_notes.md`.

**What the harness proves:** the datasets conform to the Pydantic
schemas, the extraction stub's input contract is callable with every
summary, the scenario labels use the locked `Recommendation` enum with
full label coverage, and the protocols carry the fields a future
ingestion adapter needs — all offline and fully synthetic.

**What it does NOT prove:** any clinical correctness, real extraction
quality (the agent is a placeholder stub), real trial matching accuracy,
or anything about real ClinicalTrials.gov data. It is a schema/contract
harness, not clinical decision support.

## How to run tests

From the `clarify_trial_agent/` directory:

```bash
pip install -r requirements.txt
pytest
```

All tests verify the locked rule mappings, recommendation precedence,
global missing-variable deduplication, that
`examples/demo_patient_session.json` validates against the
`PatientSession` model, and that the three synthetic datasets load and
satisfy their contracts.

## How to run the end-to-end dry-run demo

Deterministic pipeline walkthrough on synthetic data — no LLM calls, no
API keys, no network (see `docs/end_to_end_demo_notes.md`):

```bash
python scripts/run_end_to_end_demo.py
```

It prints a stage-by-stage console walkthrough and writes
`outputs/end_to_end_demo_summary.md`.

## How to run the patient profile extraction demo

Deterministic offline extraction (regex/keyword fallback of the Patient
Profile Understanding Agent — no LLM, no API keys; see
`docs/patient_profile_extraction_notes.md`):

```bash
python scripts/run_patient_profile_extraction_demo.py
```

It extracts structured, Pydantic-validated `PatientProfile`s from
synthetic summaries (age, sex, obvious diagnosis/stage; unknowns kept
explicit) and writes `outputs/patient_profile_extraction_demo.md`. The
LLM version will use `prompts/patient_profile_extraction.md`.

## How to run the criteria parser demo

Deterministic offline parsing (Criteria Parser Agent fallback — no LLM,
no API keys; see `docs/criteria_parser_notes.md`):

```bash
python scripts/run_criteria_parser_demo.py
```

It renders the mock protocols as raw trial text, parses them into typed
`Criterion` objects with stable ids and canonical `required_variables`
(inclusion/exclusion preserved; description never becomes a criterion),
and writes `outputs/criteria_parser_demo.md`. The LLM version will use
`prompts/criteria_parsing.md`.

## How to run the criterion matching demo

Deterministic offline matching (Criterion Matching Agent fallback — no
LLM, no API keys; see `docs/criterion_matching_notes.md`):

```bash
python scripts/run_criterion_matching_demo.py
```

It chains the implemented fallbacks end to end — profile extraction →
criteria parsing → per-criterion matching → effect derivation via the
locked rules → global missing-variable dedup — and writes
`outputs/criterion_matching_demo.md`. Missing information becomes
`unknown` with a `missing_variable_key`, never negative evidence. The
LLM version will use `prompts/criterion_matching.md`.

## How to run the synthetic data validator

From the `clarify_trial_agent/` directory (any cwd works — the script
resolves paths from its own location):

```bash
python scripts/validate_synthetic_data.py
```

It prints validation counts and writes
`outputs/synthetic_data_validation_summary.md` (the `outputs/` directory
is created at runtime and is git-ignored).
