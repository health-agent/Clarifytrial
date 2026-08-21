# TrialGPT criterion judgment — calibrated policy

Compare the supplied patient note with every supplied criterion. Use only the patient note and trial information in the request.

Apply this four-step table in order to each `annotation_id`:

| Step | Test | Result |
|---|---|---|
| 1 | The patient clearly does not meet the premise that makes the criterion relevant. | `not applicable` |
| 2 | Direct patient-note evidence establishes that the criterion is true or false. | Use the corresponding decisive label. |
| 3 | Strong implicit evidence establishes the result, or the fact would almost certainly be documented in this kind of thorough note if present and there is no contrary clue. | Use the corresponding decisive label and state the inference. |
| 4 | None of the tests above establishes the result. | `not enough information` |

For an inclusion criterion, `included` means that the patient meets it and `not included` means that the patient does not. For an exclusion criterion, `excluded` means that the patient meets the disqualifying criterion and `not excluded` means that the patient does not.

Do not turn absence into a negative result by default. Absence is not decisive for tests that may not have been performed, exact laboratory values, dates or time windows, undocumented history, treatment response, future willingness, or facts that a short note could reasonably omit. Preserve `not enough information` in those cases.

Cite every relevant patient-note sentence ID, or an empty list when the decision rests on justified absence. Give a brief evidence-based explanation. Return exactly one judgment for every supplied `annotation_id`, using only `allowed_labels` and the response schema supplied with the request. Do not add patient facts, medical advice, or an overall trial decision.
