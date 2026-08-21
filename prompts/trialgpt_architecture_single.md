# TrialGPT architecture benchmark — S1 strong single call

Use only `shared_input`. It contains the raw numbered patient note, every trial
criterion, and one precomputed BM25 sentence snapshot. Never infer or request a
hidden follow-up answer. Apply `missingness_rules` exactly as supplied and use
only each criterion's `allowed_labels`.

For every annotation, return the relevant patient sentence IDs, one short
evidence explanation, its label, the evidence basis, and only genuinely needed
review flags. A thorough-note absence may support `not excluded` only under the
supplied exclusion rule; it must not prove an unmentioned inclusion event,
test, date, or value.

Also return the patient-trial final status using this deterministic rule:

1. `ineligible` if any inclusion is `not included` or any exclusion is
   `excluded`;
2. otherwise `uncertain` if any label is `not enough information`;
3. otherwise `eligible`.

Return only the supplied structured response. Do not provide medical advice.
