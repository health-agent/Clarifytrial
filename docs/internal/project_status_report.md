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
2. Implement the Single-shot LLM matching path.
3. Implement Ask-all over the shared missing-variable pool.
4. Complete ClarifyTrial priority selection and targeted re-evaluation.
5. Record results after every information-acquisition action so the useful
   stopping point can be measured rather than fixed in advance.
6. Add criterion, retrieval, ranking, action-count and API-usage reports.
7. Add the optional Synthea FHIR route after the core comparison works.

## Planned comparison

| Method | Behavior |
|---|---|
| Single-shot LLM matching | recommend from the initial patient information |
| Ask-all | acquire every detected missing variable before re-evaluation |
| ClarifyTrial | acquire high-impact variables first and re-evaluate only affected criteria |

Primary metrics are criterion macro-F1 or accuracy, missing-variable Recall@k,
retrieval Recall@k, recommendation nDCG@k, unknown resolution rate, average
information-acquisition actions, API calls, token usage, cost, and latency.

## User-facing documents

- ../../README.md: integrated Korean project guide
- ../../APPLICATION_KO.md: Korean application text

All other supporting documents are kept in this internal directory.
