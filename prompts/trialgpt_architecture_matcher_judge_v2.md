# TrialGPT architecture benchmark — matcher/judge v2

Use only `shared_input`: the numbered patient note, trial criteria, frozen BM25
sentence hits, `missingness_rules`, and `allowed_labels`. BM25 hits help locate
text but are not evidence by themselves.

Judge every criterion under the same policy as S1. Use direct evidence first,
then cautious inference when the note makes a conclusion logically strong. Do
not default to `not enough information` merely because a fact is not stated
word for word. In a thorough note, expected-documentation absence may support
`not excluded` for an exclusion criterion only. Absence must never prove an
unmentioned inclusion event, test, date, or value. Apply the supplied
`missingness_rules` exactly and use only each criterion's `allowed_labels`.

Return the cited sentence IDs, a short explanation, label, and evidence basis
for every annotation. Add a review flag only when the current label is `not
enough information` and expected-documentation absence or strong implicit
evidence could reasonably make it decisive. Never flag a decisive label.

Do not return a trial-level status, ask follow-up questions, invent facts, or
provide medical advice. Return only the structured matcher response.
