# Professor-Provided Patient Input Dataset — Notes

`examples/professor_patient_summaries.json` is a professor-provided
**input robustness dataset** of 10 natural-language patient case
summaries (`{"topics": [{"num": "S001", "title": "..."}]}`). It is
treated as read-only and is kept **separate** from the project-generated
`examples/synthetic_patients.json`; neither replaces the other.

## What this dataset validates

- The **natural-language input contract** of the Patient Profile
  Understanding Agent: every summary can be passed as `summary_text` to
  `extract_patient_profile_from_summary(patient_id, summary_text)` and a
  valid `PatientProfile` is returned.
- The **schema shape**: the file validates against the existing
  `SyntheticPatientDataset` model (10 cases, `num` starting with "S",
  non-empty `title`).
- **Robustness of the extraction stub across varied summaries**: the
  cases span diverse ages (infant to elderly), specialties, and
  presentation styles, exercising the input path with realistic
  variety.

## What this dataset does NOT validate

- **Eligibility labels** — no summary carries met/unmet criteria.
- **Recommendation ground truth** — expected recommendation labels live
  exclusively in `examples/synthetic_matching_scenarios.json`.
- **Clinical correctness** — the summaries are teaching-style vignettes
  used purely as software inputs; nothing here is clinical decision
  support.

Any test asserting an expected recommendation, blocking criterion, or
missing variable from these summaries would violate the separation the
locked architecture enforces between patient inputs and rule outcomes.
