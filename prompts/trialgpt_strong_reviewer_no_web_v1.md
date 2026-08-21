# TrialGPT exact-output reviewer without web search v1

Reconsider only the supplied criteria, all of which the strong single judge
labeled `not enough information`. Start from each `initial_judgment`; do not
redo the full case or review any decisive label.

Use the same patient note, criterion meaning, label directions and
`missingness_rules` as the first judgment. Think through whether the existing
note already contains direct evidence, logically strong implicit evidence, or
a justified expected-documentation absence for an exclusion condition. Keep
`not enough information` when an exact value, score, date, time window,
unperformed test, future decision, missing event, or unresolved compound part
is still required.

The review may change an answer only when the existing patient note supports
the change. Web search and all other tools are forbidden. BM25 hits are only a
reading aid. Return one structured review for every supplied `annotation_id`,
cite only patient sentence IDs, and do not invent facts or provide medical
advice.
