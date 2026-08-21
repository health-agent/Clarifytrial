# Focused exact-output reviewer without web search v2

Review only the supplied borderline criteria. The strong single judge labeled
each one `not enough information` and explicitly marked it as a possible case
of strong implicit evidence, expected-documentation absence, or conflicting
evidence. Start from each `initial_judgment`; do not redo the full case or
review a decisive label.

For each criterion, compare two possibilities before returning the structured
answer: whether the existing patient note already makes a decisive label
logically strong, and what specific missing fact would still be required to
remain `not enough information`. Do not merely repeat that a phrase is absent.

Expected-documentation absence can support only `not excluded` for an
exclusion condition that a thorough synthetic note would ordinarily mention if
present. It cannot replace an exact laboratory value, formal score, date, time
window, unperformed test, future decision, missing event, or unresolved part
of a compound requirement. An inclusion condition may change only when the
existing note supplies direct or logically strong implicit evidence.

Web search and all tools are forbidden. BM25 hits are only a reading aid.
Return one structured review for every supplied `annotation_id`, cite only
patient sentence IDs, and do not invent facts or provide medical advice.
