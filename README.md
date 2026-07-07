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
│   └── demo_patient_session.json          # synthetic session, model-valid
├── docs/
│   ├── architecture.md
│   └── state_transition.md
└── tests/
    ├── test_effect_rules.py
    ├── test_recommendation_rules.py
    ├── test_missing_variable_dedup.py
    └── test_models_validate.py
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

## How to run tests

From the `clarify_trial_agent/` directory:

```bash
pip install -r requirements.txt
pytest
```

All tests verify the locked rule mappings, recommendation precedence,
global missing-variable deduplication, and that
`examples/demo_patient_session.json` validates against the
`PatientSession` model.
