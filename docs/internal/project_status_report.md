# ClarifyTrial Internal Project Status

## Summary

ClarifyTrial is a shared-state multi-agent prototype for interactive clinical
trial recommendation. It matches patient information against trial criteria,
tracks unknown criteria as missing variables, acquires additional information,
re-evaluates affected criteria, and produces ranked recommendations with
evidence.

The repository currently provides typed state models, deterministic rule
functions, heuristic offline stages, synthetic examples, runnable demos, tests,
and CI. Public dataset adapters and LLM-backed end-to-end evaluation remain to
be implemented.

## Implemented

| Layer | Current state |
|---|---|
| Shared state | PatientSession stores one patient and multiple trial states |
| Criterion model | met, unmet, unknown, conflict and not_applicable states |
| Rule layer | inclusion/exclusion effects, hard blocks, ranking and review paths |
| Missing information | global deduplication by missing_variable_key |
| Clarification | deterministic question templates and a global queue |
| Answer contracts | normalized answer and targeted re-evaluation interfaces |
| Demo | deterministic synthetic end-to-end walkthrough |
| Validation | pytest suite and GitHub CI on supported Python versions |

Clarification rounds are tracked at session level and have no fixed upper bound
by default. Experiments may supply a stopping rule when comparing the effect of
different question counts.

## Included synthetic artifacts

- examples/demo_patient_session.json: a model-valid multi-trial session
- examples/synthetic_patients.json: generated patient-summary inputs
- examples/professor_patient_summaries.json: provided patient-summary inputs
- examples/synthetic_trial_protocols.json: mock protocol inputs
- examples/synthetic_matching_scenarios.json: deterministic rule scenarios

These artifacts validate software contracts. They are not clinical eligibility
ground truth.

## Verified external data roles

| Source | Intended role |
|---|---|
| ClinicalTrials.gov | live trial discovery and protocol criteria input |
| TrialGPT Criterion Annotations | criterion-level labels and evidence |
| Derived masked set | missing-variable, question and re-evaluation evaluation |
| TREC Clinical Trials 2021 and 2022 | retrieval and trial-ranking evaluation |
| Synthea | optional synthetic FHIR acquisition demo |

See DATA_SOURCES.md in this directory for source details and label mapping.

## Next implementation targets

1. Add ClinicalTrials.gov, TrialGPT and TREC adapters.
2. Add Boolean criteria ASTs, source-span validation and semantic missing keys.
3. Build the multi-mask interactive benchmark with separate visible and oracle
   bundles.
4. Implement No-acquisition, Fixed-order, Coverage-only, Clarify-priority and
   Ask-all policies over the same shared missing-variable pool.
5. Complete targeted re-evaluation and compare it with full re-evaluation on
   the same answer trajectories.
6. Record results after every information-acquisition action so the useful
   stopping point can be measured rather than fixed in advance.
7. Add separate criterion, interactive-policy, TREC-ranking, route and
   API-usage reports.
8. Add the optional Synthea FHIR route after the core comparison works.

## Planned comparisons

### Acquisition policy

| Policy | Behavior |
|---|---|
| No-acquisition | stop at the initial patient information |
| Fixed-order | acquire variables in a stable non-prioritized order |
| Coverage-only | prioritize the variable linked to the most criteria |
| Clarify-priority | prioritize variables capable of changing more trial states |
| Ask-all | acquire every answerable variable as an information ceiling |

All policies use the same re-evaluation engine. A separate replay experiment
holds the variable sequence and answers fixed, then compares Full and Targeted
re-evaluation for state agreement, calls, tokens, cost and latency.

Primary metrics include criterion recovery, trial-state exact match,
quality-action and quality-cost AUC, wrong commitment, non-target mutation,
TREC NDCG@10 in its separate historical track, API calls, token usage, cost,
and latency.

## User-facing documents

- ../../README.md: integrated Korean project guide
- ../../APPLICATION_KO.md: Korean application text

All other supporting documents are kept in this internal directory.
