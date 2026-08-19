# Repository Agent Notes

- Keep all patient examples synthetic. Do not commit PHI, credentials, raw EHR
  exports, API keys, or private trial sponsor material.
- Preserve the core contract: recommendations must include evidence and missing
  information questions, not only natural-language answers.
- Prefer deterministic tests around eligibility behavior before adding LLM calls.
- When adding external data sources, document licensing, retrieval date, and API
  terms in `DATA_SOURCES.md`.
- Any clinical-facing output must retain the disclaimer in
  `MEDICAL_DISCLAIMER.md`.

