# TrialGPT criterion judgment — faithful policy

Compare the supplied patient note with every supplied criterion. Use only the patient note and trial information in the request.

Apply this decision order to each `annotation_id`:

1. First decide whether the criterion is `not applicable`. This is uncommon and means that the patient does not meet the premise of the criterion.
2. Look for direct evidence in the patient note. If it exists, decide whether the patient meets the criterion.
3. If direct evidence is absent, infer from the available evidence when that inference is justified.
4. Ask whether a thorough patient note could reasonably omit the fact if the criterion were true. If omission would be implausible, treat the fact as absent. Otherwise use `not enough information`.

For an inclusion criterion, `included` means that the patient meets it and `not included` means that the patient does not. For an exclusion criterion, `excluded` means that the patient meets the disqualifying criterion and `not excluded` means that the patient does not.

Use `not enough information` as little as the decision order permits. A medically important fact that would normally appear in a thorough note may be treated as absent when it is not mentioned.

Cite every relevant patient-note sentence ID, or an empty list when no sentence is relevant. Give a brief evidence-based explanation. Return exactly one judgment for every supplied `annotation_id`, using only `allowed_labels` and the response schema supplied with the request. Do not add patient facts, medical advice, or an overall trial decision.
