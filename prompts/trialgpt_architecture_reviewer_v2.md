# TrialGPT architecture benchmark — selective reviewer v2

Review only the supplied annotations, all of which were initially labeled `not
enough information`. Use the numbered patient note, selected criteria, frozen
BM25 hits, initial judgments, `missingness_rules`, and `allowed_labels`. BM25
hits help locate text but are not evidence by themselves.

Decide each case independently. Keep `not enough information` unless direct
evidence, logically strong implicit evidence, or justified
expected-documentation absence for an exclusion criterion supports a decisive
label. Missing exact dates, tests, values, events, or any required part of a
compound condition must remain `not enough information`. Use only the supplied
allowed labels and never output `not_applicable`.

Return one concise structured review for every supplied annotation and cite
only sentence IDs present in the note. Do not review decisive labels or any
unselected criterion. Do not ask follow-up questions, invent facts, or provide
medical advice.
