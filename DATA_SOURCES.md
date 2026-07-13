# Data Sources and External Services

Last verified: 2026-07-11

ClarifyTrial uses only synthetic patient data and public clinical-trial
metadata for research and demonstration. Do not commit raw EHR exports,
credentials, private sponsor material, or identifiable patient data.

## Verified sources

| Source | Planned use | Verified facts | License or terms | Retrieval policy |
|---|---|---|---|---|
| [ClinicalTrials.gov API](https://clinicaltrials.gov/data-about-studies/learn-about-api) | Recruiting trial discovery and protocol metadata | API v2 exposes recruitment status and `eligibilityCriteria`; a live recruiting-study request succeeded on 2026-07-11 | [ClinicalTrials.gov Terms and Conditions](https://clinicaltrials.gov/about-site/terms-conditions): attribution, processing date and modification disclosure required; data change regularly | Record query, `dataTimestamp`, NCT IDs, response hash and processing date. Do not commit or redistribute a stale full-registry copy; keep run caches outside Git |
| [TrialGPT Criterion Annotations](https://huggingface.co/datasets/ncbi/TrialGPT-Criterion-Annotations) | Criterion-level expert labels and evidence sentences | 1,015 rows, 53 patients, 103 trials; includes `expert_eligibility` and `expert_sentences` | Public domain; cite the [TrialGPT paper](https://www.nature.com/articles/s41467-024-53081-z) | Download through a script or documented command; record source revision and SHA-256 |
| [TrialGPT code and preprocessed data](https://github.com/ncbi-nlp/TrialGPT) | Reference retrieval, matching and ranking implementation | Public code and TREC/SIGIR adapters are available | NCBI public-domain notice; preserve citation and disclaimer | Do not vendor the full repository; record the commit used |
| [TREC Clinical Trials 2021](https://trec.nist.gov/data/trials2021.html) | Trial-level ranking and eligible/excluded evaluation | qrels labels: 0 non-relevant, 1 excluded, 2 eligible | Cite TREC and follow corpus-specific download conditions | Use qrels with the corresponding 2021 corpus, not current API records. Do not treat unjudged trials as verified negatives |
| [TREC Clinical Trials 2022](https://trec.nist.gov/data/trials2022.html) | Independent trial-level ranking evaluation | qrels use the same 0/1/2 labels | Cite TREC and follow corpus-specific download conditions | Use the corresponding 2022 corpus and keep evaluation separate from live operational filters; official assessment ignored recruitment status and location |
| [Synthea](https://github.com/synthetichealth/synthea) | Optional structured EHR acquisition demo | Generates synthetic records in FHIR R4, STU3, DSTU2, Bulk FHIR, CSV and other formats | Apache-2.0 | Pin generator version and random seed; use as an acquisition-path demo, not the primary eligibility gold set |

## Derived masked-incomplete benchmark

This dataset does not exist as a ready-made public benchmark. It will be
derived from TrialGPT Criterion Annotations under the following rules:

1. Split development and holdout by `patient_id` before masking or template work.
2. Select explicit met/unmet evidence; do not use source `unknown` or
   `not_applicable` rows as answer-after-mask recovery cases.
3. Build session-level multi-mask examples with 2–5 independent variables,
   including budget-binding cases with more than three missing variables.
4. Remove every equivalent evidence span for the masked fact, not only one
   linked sentence. Reject cases inferable from the remaining visible context.
5. Use neutral variable keys such as `egfr_value`; never encode polarity or
   criterion satisfaction in the key.
6. Store hidden answers as independently normalized typed objects rather than
   copying the removed sentence verbatim.
7. Physically separate visible input from original notes, removed spans and
   hidden values. Hidden content must not enter prompts, RAG indexes, cache
   keys, filenames, debug logs or model-visible traces.
8. Record affected trial and criterion IDs without storing the answer value or
   resulting met/unmet label in the visible manifest.
9. Validate questions against exactly one intended variable; mismatches and
   multi-variable questions receive `no_answer`.
10. Require the counterfactual contract: full note reproduces the reference
    label, masked note becomes unknown, and masked note plus hidden answer
    reproduces the reference label without changing unrelated criteria.
11. Keep question generation, hidden-answer construction and holdout audit
    independent.
12. Audit all holdout sessions and a development sample for leakage and
    residual inferability.

The derived artifact must record its parent dataset revision, transformation
script version, random seed, row identifiers and SHA-256. Model-generated
answers are never used as their own gold labels.

## Label compatibility

TrialGPT does not provide a `conflict` gold label. Map labels as follows:

| Source label | ClarifyTrial label |
|---|---|
| inclusion `included` | `met` |
| inclusion `not included` | `unmet` |
| exclusion `excluded` | `met` |
| exclusion `not excluded` | `unmet` |
| `not enough information` | `unknown` |
| `not applicable` | `not_applicable` |

Evaluate `conflict` only on separately constructed and reviewed synthetic
cases. TREC qrels are trial-level judgments and must not be reused as
criterion-level or follow-up-question gold.

Adapters must preserve three separate layers:

- `source_label`: the dataset's original label;
- `criterion_status`: `met`, `unmet`, `unknown`, `not_applicable`, or
  `conflict`;
- `eligibility_effect`: `supports_eligibility`, `blocks_eligibility`,
  `uncertain`, or `neutral`, plus separate review fields.

An exclusion criterion mapped to `met` must become
`blocks_eligibility` downstream. `not_applicable` remains distinct from
`unknown`; it maps to `neutral` in the deterministic rules.

## External model service

[Upstage pricing](https://www.upstage.ai/pricing/api), checked on 2026-07-11,
lists Solar Pro 3 at USD 0.15 per million input tokens, USD 0.015 per million
cached input tokens, and USD 0.60 per million output tokens, excluding VAT.
Provider pricing and account-specific credits can change, so every experiment
must store the pricing snapshot date and actual token usage.

No API key, bearer token, raw response containing credentials, or account
identifier may be committed. Use environment variables and sanitized logs.

## Required provenance fields

Every downloaded or generated dataset manifest must include:

- `source_name`
- `source_url`
- `source_revision` or `retrieved_at`
- `data_timestamp` when the source exposes one
- `license_or_terms_url`
- `sha256`
- `transformation_script`
- `transformation_version`
- `synthetic_or_public_metadata`
