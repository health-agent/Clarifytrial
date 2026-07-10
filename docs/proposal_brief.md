# Proposal Brief — ClarifyTrial Agent

## Project title

**ClarifyTrial Agent: a shared-state multi-agent system for Interactive
Clinical Trial Recommendation** (architecture v1.2-final).

## Problem statement

Matching a patient to clinical trials is not a single question-answering
task. Real eligibility assessment is interactive and stateful: a patient
summary rarely contains every variable that trial criteria require, the
same missing variable (e.g. performance status) blocks criteria in many
trials at once, and evidence can be absent or contradictory. A one-shot
LLM answer hides these gaps; a safe system must track what is known,
unknown, and conflicting per criterion and per trial, ask targeted
follow-up questions, and escalate to a human when evidence conflicts or
questions run out.

## Core idea

Separate **language understanding** from **eligibility decision-making**.
LLM agents only normalize inputs (parse criteria, extract patient
variables, phrase questions, explain results). Whether a criterion
supports or blocks eligibility, and what the final recommendation is,
comes exclusively from locked, deterministic, unit-tested rules over a
central shared state. Every conclusion is reproducible and auditable.

## Why this is not a simple chatbot

- A chatbot has a transcript; ClarifyTrial has a **typed session state**
  (`PatientSession`) tracking every criterion of every trial with an
  explicit five-value match status (met / unmet / unknown / conflict /
  not_applicable).
- A chatbot answers once; ClarifyTrial runs a **bounded clarification
  loop** (session-level, max 3 rounds) whose questions are generated from
  a deduplicated pool of concretely missing variables, then performs
  **targeted re-evaluation** of only the affected criteria.
- A chatbot's reasoning is opaque; here eligibility effects and the final
  recommendation come from **pure functions** (`rules.py`) with exact,
  tested mappings, and conflicts route to `needs_human_review` instead of
  being smoothed over.

## Architecture pillars (locked v1.2-final)

- **Shared-state Eligibility State Tracker** — the single owner of the
  session state, keyed by `patient_id`.
- **Multi-trial state** — `trial_states_by_trial_id`; each trial carries
  its own `trial_context` and `criterion_state[]`, evaluated in parallel
  within one session.
- **Global missing-variable pool** — unknowns from all trials are
  deduplicated by `missing_variable_key`, so the patient is asked about
  ECOG status once, not once per trial.
- **Global clarification queue** — questions are managed globally with
  priority ranks, never per trial; rounds are capped at 3.
- **Answer-based re-evaluation** — free-text answers are normalized by
  the Patient Profile Understanding Agent before any rule update, then
  only the criteria affected by the answered variable are re-evaluated.
- **Trial ranking and recommendation precedence** — per trial, applied
  exactly in order: any blocking criterion → likely_ineligible; else any
  review flag → needs_human_review; else high uncertainty → uncertain;
  else likely_eligible. A trial relevance score influences ranking order
  only and can never override a hard block.
- **Evidence and reviewability** — each criterion state can carry an
  `EvidenceContext` (source sentences + profile fields used); conflicts
  and exhausted question rounds set `review_required` with a typed
  reason; every agent action is loggable via `RequestLog`.

## Current implementation progress

Implemented and verified today: the complete Pydantic v2 schema layer
(21 models, 7 enums), the complete deterministic rule layer, typed stubs
for all 10 agents, four synthetic datasets (including a professor-provided
patient input set), Mermaid architecture and state-transition diagrams,
a dataset validation script, and **40 passing pytest tests** covering
every rule mapping, the full recommendation precedence, global
deduplication, schema validity of all examples, and the natural-language
input contract. Not yet implemented (by design, in order of planned
work): state-tracker mutations, deterministic mock matching, LLM-based
agent internals, and a ClinicalTrials.gov API v2 ingestion adapter. No
real patient data is used anywhere.

## Expected deliverables

1. **Now (done):** locked architecture skeleton + self-verifying test
   harness (this repository).
2. **Next:** deterministic end-to-end pipeline (no LLM) that replays the
   labeled synthetic matching scenarios and reproduces their expected
   recommendation labels.
3. **Then:** LLM-backed agent implementations behind the same typed
   contracts, evaluated against the same harness.
4. **Later:** ClinicalTrials.gov API v2 ingestion adapter and richer
   synthetic patients (e.g. Synthea) — both already anticipated by the
   current schemas, neither implemented today.
