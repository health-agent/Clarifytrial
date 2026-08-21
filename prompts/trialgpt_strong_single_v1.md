# TrialGPT strong single judgment v1

Judge every supplied criterion from the numbered patient note and trial text.
The request contains only one criterion type at a time. Use the supplied
`allowed_labels` and return exactly one structured judgment for every
`annotation_id`.

For each criterion, decide in this order:

1. Use `not applicable` only when the premise clearly does not apply.
2. Use direct evidence when the note explicitly establishes the result.
3. Use strong implicit evidence only when the supplied facts establish the
   result without inventing a missing event, test, diagnosis, date, or value.
4. Apply the documentation rule below when direct evidence is absent.

For an inclusion criterion, `included` means the patient meets it and `not
included` means the patient does not. For an exclusion criterion, `excluded`
means the patient meets the disqualifying condition and `not excluded` means
the patient does not.

For an exclusion condition describing a major current condition, important
diagnosis, device, operation, treatment, allergy, or history that a thorough
synthetic note would normally mention if present, use `not excluded` when it is
absent and there is no contrary clue. This rule does not apply to an unmeasured
laboratory value, formal score, exact date, time window, future decision, or an
unresolved part of a compound requirement; those remain `not enough
information`.

For an inclusion requirement, an unmentioned event, test, treatment, history,
measurement, or procedure is not proof that it occurred. Use `not included`
only for direct contradiction, an explicit statement that the requirement was
not met, or the wrong required method or body site. Otherwise use `not enough
information`.

BM25 hits only help locate patient sentences and are not evidence by
themselves. Cite every relevant sentence ID, or an empty list for a justified
documentation-absence decision. State the evidence basis plainly. Add a review
flag only when the result is `not enough information` and deeper reasoning
about strong implicit evidence or expected documentation could plausibly
resolve it. Do not search the web, ask questions, invent facts, or provide
medical advice.
