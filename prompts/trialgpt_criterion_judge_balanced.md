# TrialGPT criterion judgment — balanced policy

Compare the supplied patient note with every supplied criterion. Use only the patient note and trial information in the request. Treat this as the criterion-labeling task defined below, not as a final enrollment decision.

For each `annotation_id`, decide in this order:

1. Use `not applicable` only when the premise of the criterion clearly does not apply to the patient.
2. Use direct patient-note evidence when it establishes that the criterion is met or not met.
3. Use strong implicit evidence only when the supplied facts establish the result without inventing a missing event, test, diagnosis, date, or measurement.
4. Apply the documentation rule below when direct evidence is absent.

For an inclusion criterion, `included` means that the patient meets it and `not included` means that the patient does not. For an exclusion criterion, `excluded` means that the patient meets the disqualifying condition and `not excluded` means that the patient does not.

## Documentation rule

- For an exclusion condition describing a major current condition, important diagnosis, device, operation, treatment, allergy, or medical history that a thorough synthetic note would normally mention, use `not excluded` when it is absent and there is no contrary clue.
- For an inclusion requirement, do not assume that an unmentioned required test, treatment, history, measurement, or event occurred. Use `not included` when the note directly contradicts the requirement, explicitly says the required event or test did not occur, or provides the wrong test method or body site. Otherwise use `not enough information`.
- Keep `not enough information` for missing exact laboratory values, formal scores, dates, time windows, unperformed tests, future willingness, future clinician decisions, or a compound condition with an unresolved required part.
- A general statement that the patient can consent and comply may support ordinary ability or willingness requirements, but it does not prove an unperformed medical test or procedure.

Examples of the rule:

- An exclusion criterion asks about a major implanted device and a thorough acute note does not mention one: `not excluded`.
- An exclusion criterion requires a laboratory value beyond a cutoff but the value was not measured: `not enough information`.
- An inclusion criterion requires a finding on a specific imaging method, while the note gives a different imaging method or a different body site: `not included`.
- An inclusion criterion requires an exact score or time period that the note does not provide: `not enough information`.

Cite every relevant patient-note sentence ID, or an empty list when a justified documentation-absence decision has no relevant sentence. Give one brief explanation that states whether the decision used direct evidence, strong implicit evidence, justified documentation absence, or unresolved information. Return exactly one judgment for every supplied `annotation_id`, using only `allowed_labels` and the response schema supplied with the request. Do not add patient facts, medical advice, or an overall trial decision.
