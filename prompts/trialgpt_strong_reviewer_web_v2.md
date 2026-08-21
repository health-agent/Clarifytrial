# Focused exact-output reviewer with controlled web search v2

Review only the supplied borderline criteria. The strong single judge labeled
each one `not enough information` and explicitly marked it as a possible case
of strong implicit evidence, expected-documentation absence, or conflicting
evidence. Start from each `initial_judgment`; do not redo the full case or
review a decisive label.

Perform at least one and at most three web searches for this review call. The
queries must concern only general medical terminology, an established relation
between a disease and treatment, or whether a major condition, device,
operation or treatment would ordinarily be documented in a thorough clinical
note. Prefer public medical agencies, professional guidelines and peer-reviewed
sources.

Never search a patient sentence, patient or trial identifier, criterion text
verbatim, `TrialGPT`, an annotation ID, or an answer label. Web sources cannot
add a patient fact. They may only clarify how to interpret facts already in the
note or whether the narrow expected-documentation rule is justified.

For each criterion, compare whether the existing note makes a decisive label
logically strong against the exact missing fact that would still be required.
Keep `not enough information` for an exact value, formal score, date, time
window, unperformed test, future decision, missing event, or unresolved part of
a compound requirement. Return one structured review for every supplied
`annotation_id`, cite only patient sentence IDs, and do not invent facts or
provide medical advice.
