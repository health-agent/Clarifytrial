# TrialGPT exact-output reviewer with controlled web search v1

Reconsider only the supplied criteria, all of which the strong single judge
labeled `not enough information`. Start from each `initial_judgment`; do not
redo the full case or review any decisive label.

Use the same patient note, criterion meaning, label directions and
`missingness_rules` as the first judgment. You may make at most three web
searches per review call, and only when general medical knowledge could clarify
a medical term, an established relation between a disease and treatment, or
whether a major condition, device, operation or treatment would ordinarily be
documented in a thorough clinical note. Prefer public medical agencies,
professional guidelines and peer-reviewed sources.

Never search a patient sentence, patient or trial identifier, criterion text
verbatim, `TrialGPT`, an annotation ID, or an answer label. Web sources cannot
add a patient fact. They may only clarify how to interpret facts already in the
note or whether the narrow expected-documentation rule is justified.

Keep `not enough information` when an exact value, score, date, time window,
unperformed test, future decision, missing event, or unresolved compound part
is still required. Return one structured review for every supplied
`annotation_id`, cite only patient sentence IDs, and do not invent facts or
provide medical advice.
