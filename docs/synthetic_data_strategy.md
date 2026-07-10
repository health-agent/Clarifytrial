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

## Future public trial protocol source (not implemented)

`TrialProtocol` is source-agnostic but already carries the fields a future
**ClinicalTrials.gov API v2 ingestion adapter** would populate (`nct_id`,
`eligibility_criteria_raw`, `conditions`, `interventions`, `source`,
`source_url`, `retrieved_at`). No API is called anywhere in this project
and no API response paths are assumed; the mock dataset stands in for that
adapter's output.

## Future richer synthetic patients (not added now)

Synthetic EHR generators such as **Synthea** could later provide richer
structured patient profiles (longitudinal records, coded conditions,
medications, labs) that map onto `PatientProfile.variables`. That would
strengthen extraction and matching tests without ever involving real
patient data. It is future work and intentionally not part of this
harness.
