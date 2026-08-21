# TrialGPT criterion judgment

Compare the supplied synthetic patient note with every supplied criterion. Use only the patient note and trial information in the request.

For each `annotation_id`:

1. Decide whether the criterion applies to this patient.
2. Cite every relevant patient-note sentence ID. Use an empty list when no sentence is relevant.
3. Give one short evidence-based explanation.
4. Choose exactly one label from `allowed_labels`.

Use `not enough information` when the note cannot support a decision. If a thorough clinical note would normally mention a medically important fact when it is present, you may treat its absence as evidence that the fact is not present; otherwise keep the result as `not enough information`. Use `not applicable` only when the premise of the criterion does not apply to the patient.

Return exactly one judgment for every supplied `annotation_id`. Do not add facts, medical advice, or an overall trial decision.
