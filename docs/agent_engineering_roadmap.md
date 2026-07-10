# Agent Engineering Roadmap — Skeleton to Portfolio Project

_A concrete, milestone-based plan for turning the current v1.2-final
skeleton into a working agentic AI portfolio project before the final
challenge submission. Timeline: ~6 weeks of focused development plus a
final review/polish buffer. Grounded in the actual repository state as of
2026-07-07._

---

## 1. Current baseline

Already implemented in this repo:

- **Schema layer** — `models.py`: 21 Pydantic v2 models + 7 locked enums
  encoding every architecture invariant (session state keyed by
  `patient_id`, `trial_states_by_trial_id`, global missing-variable pool,
  global clarification queue, schema-enforced 3-round cap).
- **Deterministic rule layer** — `rules.py`: effect mapping, global
  dedup, recommendation precedence, ranking. Complete and fully tested.
- **Agent stubs** — `agents/`: 10 modules with typed contracts and
  TODOs. Two already delegate to real rules
  (`missing_information_detection`, `trial_recommendation`); one returns
  a working placeholder (`extract_patient_profile_from_summary`); the
  rest raise `NotImplementedError`.
- **Synthetic data harness** — 4 datasets in `examples/` plus
  `scripts/validate_synthetic_data.py`.
- **Professor dataset** — `examples/professor_patient_summaries.json`,
  read-only input robustness set (10 vignettes).
- **Docs** — architecture/state diagrams, data strategy, status report,
  proposal brief, demo script, evidence table, application answers.
- **40 passing tests** across 8 files.

What the tests prove: the decision core is correct (every effect mapping,
both review paths, all four precedence outcomes, relevance never
overriding blocks, global dedup) and all schemas/datasets are coherent.

What they do NOT prove: no end-to-end pipeline runs yet; extraction is a
placeholder; the labeled matching scenarios are validated for shape, not
reproduced by execution; nothing about clinical accuracy or real data.

## 2. Final portfolio target

The completed project should demonstrate, on synthetic data only:

> Paste a patient summary → structured profile extraction → parallel
> evaluation of multiple mock trials criterion-by-criterion → targeted
> clarification questions (deduplicated, max 3 rounds) → answer
> normalization and partial re-evaluation → ranked recommendations with
> per-criterion evidence and human-review escalation — with every
> decision traceable to a pure rule and every LLM call logged.

Why it is portfolio-relevant (and more than a chatbot):

- **Agent state tracking** — a typed, multi-trial session state machine,
  not a transcript.
- **Uncertainty handling** — unknown/conflict are first-class statuses
  with distinct downstream behavior, driven by an uncertainty-ratio
  threshold.
- **Human-in-the-loop clarification** — a bounded, prioritized question
  loop plus explicit `needs_human_review` escalation with typed reasons.
- **Evidence-grounded recommendation** — `EvidenceContext` per criterion
  and an auditable rule trail; the LLM never decides eligibility.

## 3. 6-week implementation roadmap + final buffer

Realistic pace: each week assumes part-time focused work; every week ends
with `python -m pytest -q` green (existing 40 tests never break) plus new
tests for that week's layer.

### Week 1 — Runnable end-to-end dry run, no LLM

- **Deliverables**: `scripts/run_dry_pipeline.py` — a deterministic
  pipeline that loads `synthetic_trial_protocols.json` and
  `synthetic_matching_scenarios.json`, evaluates criteria by reading
  `PatientProfile.variables` directly (simple comparisons, no LLM),
  builds `PatientSession`s, and produces ranked `TrialRecommendation`s.
  Implement the Eligibility State Tracker mutations
  (`register_trial`, `update_criterion_status`,
  `increment_clarification_round`) — pure Python.
- **Exit criteria**: the dry run reproduces the `expected_recommendation`
  of all 6 labeled scenarios; new pytest file asserts this.
- **Builds on**: `rules.py` (unchanged), `models.PatientSession`,
  `agents/eligibility_state_tracker.py` stubs,
  `examples/synthetic_matching_scenarios.json`.

### Week 2 — Patient Profile Understanding Agent (LLM structured extraction)

- **Deliverables**: real `extract_patient_profile_from_summary` using an
  LLM with schema-constrained output (Pydantic-validated JSON), behind
  the existing typed contract; a config switch to keep the offline
  placeholder for tests; extraction run over all 20 summaries (10
  synthetic + 10 professor) with results saved to `outputs/`.
- **Exit criteria**: all 20 summaries extract to valid `PatientProfile`s
  without manual fixes; offline tests still pass without network.
- **Builds on**: `agents/patient_profile_understanding.py`,
  `examples/synthetic_patients.json`,
  `examples/professor_patient_summaries.json`, `RequestLog` for call
  logging.

### Week 3 — Criteria Parser Agent (structured extraction)

- **Deliverables**: `parse_criteria(protocol)` returning validated
  `Criterion` lists (correct `criterion_type`, sensible
  `required_variables`) from `eligibility_criteria_raw`; parsed output
  for all 3 mock protocols checked into `outputs/` (or a fixtures file)
  for regression comparison.
- **Exit criteria**: parsed criteria for the 3 mock trials match a
  hand-reviewed golden file; `required_variables` use canonical
  snake_case keys consistent with scenario variables (critical for
  dedup later).
- **Builds on**: `agents/criteria_parser.py`, `models.Criterion`,
  `models.TrialProtocol`, `examples/synthetic_trial_protocols.json`.

### Week 4 — Criterion Matching Agent (schema-constrained LLM output)

- **Deliverables**: `match_criterion` returning a
  `CriterionMatchStatus` (+ optional `EvidenceContext`) from an LLM
  constrained to the five-value enum; wire into the Week-1 pipeline so
  effects still come only from `rules.derive_eligibility_effect`;
  comparison harness: LLM matcher vs. Week-1 deterministic matcher on
  the labeled scenarios.
- **Exit criteria**: LLM-backed pipeline reproduces ≥ 5/6 scenario
  labels; every disagreement is logged and explained; rules layer
  untouched.
- **Builds on**: `agents/criterion_matching.py`,
  `agents/evidence_context_builder.py`, `models.EvidenceContext`,
  Week 1–3 outputs.

### Week 5 — Missing information + clarification loop

- **Deliverables**: end-to-end loop: unknown statuses →
  `deduplicate_missing_variables` → LLM question generation
  (`generate_questions`) → scripted synthetic answers →
  `normalize_clarification_answer` → targeted re-evaluation of affected
  criteria only; round counter enforced via the tracker.
- **Exit criteria**: a scenario starting with 2+ missing variables
  reaches a final recommendation within ≤ 3 rounds; a variable shared by
  two trials is asked exactly once; unknowns at round 3 route to
  `needs_human_review` with `max_rounds_exceeded`.
- **Builds on**: `agents/missing_information_detection.py`,
  `agents/clarification_question.py`,
  `agents/answer_update_reevaluation.py`, `models.FollowUpQuestion`,
  `models.AnswerUpdate`.

### Week 6 — Ranking, explanation, evaluation metrics, demo integration

- **Deliverables**: `explain_results` producing plain-language
  per-trial explanations grounded in blocking/supporting/uncertain
  criteria (`FinalOutput`); an evaluation script reporting label
  accuracy on scenarios, question-efficiency (rounds used, dedup rate),
  and cost per session from `RequestLog` data; a single demo command
  (e.g. `python scripts/run_demo.py --patient S001`).
- **Exit criteria**: one command runs the full story end to end on
  synthetic data and writes a readable report to `outputs/`; metrics
  table included in README.
- **Builds on**: `agents/result_explanation.py`,
  `agents/trial_recommendation.py` (already rule-backed),
  `models.FinalOutput`, `models.RequestLog`.

### Final buffer (post-Week 6) — review, polish, submission

- Bug fixing and flaky-prompt hardening; freeze prompts.
- Documentation pass: update README, status report, evidence table with
  actual metrics; re-verify every claim against the repo.
- Presentation: refresh `docs/demo_script.md` with the live demo flow;
  record a fallback demo run (saved outputs) in case of API issues.
- Final submission checklist: clean `pytest` run, validator run, fresh
  clone smoke test, no secrets in repo (`.env` git-ignored).

## 4. Learning objectives by milestone

| Milestone | Learning objectives |
|---|---|
| Week 1 | **Pydantic validation** as a state-machine backbone; **state management** — immutable-ish session updates through one owner (the tracker) |
| Week 2 | **Structured outputs** (schema-constrained generation, retry-on-invalid); **prompt design** for extraction; **cost control** — small model for bulk extraction, measuring tokens per call |
| Week 3 | **Prompt design** for decomposition tasks; canonical key naming as a data-contract problem; golden-file regression testing |
| Week 4 | Enum-constrained outputs; **error handling** — invalid/ambiguous LLM output must degrade to `unknown`/`conflict`, never crash or guess; separating judgment (LLM) from decision (rules) |
| Week 5 | Loop control and termination proofs (3-round cap); **evaluation design** — question efficiency and dedup rate as metrics |
| Week 6 | **Evaluation design** — label accuracy vs. deterministic baseline; **observability** — every LLM call emits a `RequestLog` (latency, model_version, error_code) and cost is reported per session |
| Buffer | Reproducibility discipline: fresh-clone test, pinned versions, documented run commands |

## 5. Do-not-do-yet list

Deferred until the core pipeline works end to end (Week 6 exit):

- **FastAPI / any web service** — adds deployment surface without proving
  agent behavior; the demo script is the interface for now.
- **UI** — same reason; a readable `outputs/` report demos better than a
  half-built frontend.
- **Full LangGraph (or other orchestration frameworks)** — the tracker +
  plain Python loop is sufficient at this scale; adopting a framework
  before the loop semantics are proven risks reworking state ownership.
- **ClinicalTrials.gov live API** — the locked scope is synthetic-only;
  `TrialProtocol` already carries adapter-ready fields, so ingestion can
  be added later without schema changes.
- **Full RAG** — criteria and summaries fit in context at this scale;
  retrieval adds failure modes without adding demonstrated capability.
- **Fine-tuning** — no evidence yet that prompting is insufficient; cost
  and time are far beyond the USD 70 budget.

## 6. Next 3-day action plan

- **Day 1** — write `scripts/run_dry_pipeline.py` + the tracker
  mutations (Week 1 core): deterministic evaluation of the 6 labeled
  scenarios, assert expected labels, add the pytest file. All offline;
  zero API cost.
- **Day 2** — implement ONE real LLM structured-extraction agent
  (`extract_patient_profile_from_summary`) behind the existing contract
  with an offline fallback; run it on 2–3 summaries only (budget-safe),
  logging each call via `RequestLog`.
- **Day 3** — add a single demo command that chains Day 1 + Day 2 on one
  patient; update README with the run instructions and a current status
  line; finalize the proposal form using
  `docs/application_answers.md`.

_Rule for every step: `rules.py` and the locked schema invariants never
change; all 40 existing tests must stay green after every commit._
