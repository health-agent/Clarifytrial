# Synthetic Data Strategy

All data in this project is synthetic/mock. Nothing here is real patient
data or clinical decision support.

## Why three separate datasets?

The harness deliberately separates three concerns, because they validate
different contracts of the locked v1.2-final architecture:

1. **Patient summaries** (`examples/synthetic_patients.json`) — professor-
   style natural-language case summaries. They are patient **INPUTS only**.
2. **Trial protocols** (`examples/synthetic_trial_protocols.json`) —
   source-agnostic mock protocols conforming to `TrialProtocol`.
3. **Matching scenarios** (`examples/synthetic_matching_scenarios.json`) —
   small **labeled** examples with expected recommendation outcomes.

Mixing these would blur the line between "what the system receives" and
"what the system should conclude", which is exactly the distinction the
architecture enforces (LLM agents normalize inputs; pure rules decide
effects and recommendations).

## What patient summaries validate — and do NOT validate

They validate the **input contract** of the Patient Profile Understanding
Agent: a summary can be passed as `summary_text` to
`extract_patient_profile_from_summary(patient_id, summary_text)` and a
valid `PatientProfile` comes back.

They do **NOT** carry eligibility ground truth. No test may treat a patient
summary as an expected recommendation label, blocking criterion, or missing
variable. A summary is raw material for extraction, nothing more.

## Why matching scenarios are needed

Recommendation labels (`likely_eligible`, `likely_ineligible`,
`uncertain`, `needs_human_review`) come from the locked precedence rules,
so testing them requires examples where the expected label — plus expected
missing variables and blocking criteria — is stated explicitly by
construction. `SyntheticMatchingScenario` provides exactly that: a valid
`PatientProfile`, a target trial, and expected outputs with an
`explanation_of_label`. These are rule-validation fixtures, not clinical
truth.

## Verified public trial protocol source (adapter not implemented)

`TrialProtocol` is source-agnostic but already carries the fields a future
**ClinicalTrials.gov API v2 ingestion adapter** will populate (`nct_id`,
`eligibility_criteria_raw`, `conditions`, `interventions`, `source`,
`source_url`, `retrieved_at`). The official API fields and a live recruiting
study response were verified on 2026-07-14, but no adapter is called anywhere
in this repository yet. Runs will record the API data timestamp, query, NCT IDs
and response hash; raw registry caches stay outside Git. TREC ranking evaluation
uses each track's corresponding historical corpus. See `DATA_SOURCES.md`.

## Planned masked incomplete-information benchmark

The public TrialGPT Criterion Annotations dataset contains 1,015 expert-reviewed
criterion rows with expert labels and evidence sentence IDs. It can support a
derived interactive benchmark, but it is not already a follow-up-question
dataset.

The benchmark masks a patient variable supported by expert evidence, records
that value as the expected patient answer, runs missing-variable detection and
question generation, and then checks whether answer-driven re-evaluation
returns to the original expert criterion label.

TrialGPT does not contain a `conflict` gold label. Conflict behavior stays in
separate synthetic and manually reviewed fixtures.

## Future richer synthetic patients (not added now)

**Synthea** can provide richer
structured patient profiles (longitudinal records, coded conditions,
medications, labs) that map onto `PatientProfile.variables`. That would
demonstrate a structured FHIR information-acquisition route without real
patient data. It is optional because generated records do not guarantee every
trial-specific biomarker or screening fact. It must not replace the TrialGPT
and TREC gold sets.
