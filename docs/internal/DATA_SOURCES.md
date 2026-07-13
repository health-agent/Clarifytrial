# Data Sources and External Services

Last verified: 2026-07-14

ClarifyTrial uses only synthetic patient data and public clinical-trial
metadata for research and demonstration. Do not commit raw EHR exports,
credentials, private sponsor material, or identifiable patient data.

## Verified sources

| Source | Planned use | Verified facts | License or terms | Retrieval policy |
|---|---|---|---|---|
| [ClinicalTrials.gov API](https://clinicaltrials.gov/data-api/about-api) | Recruiting trial discovery and protocol metadata | API v2 exposes recruitment status and `eligibilityCriteria`; a live recruiting-study request succeeded on 2026-07-14 | [ClinicalTrials.gov Terms and Conditions](https://clinicaltrials.gov/about-site/terms-conditions): attribution, processing date and modification disclosure required; data change regularly | Record query, `dataTimestamp`, NCT IDs, response hash and processing date. Do not commit or redistribute a stale full-registry copy; keep run caches outside Git |
| [TrialGPT Criterion Annotations](https://huggingface.co/datasets/ncbi/TrialGPT-Criterion-Annotations) | Criterion-level expert labels and evidence sentences | 1,015 rows; fields include `patient_id`, `note`, `trial_id`, `criterion_type`, `expert_sentences` and `expert_eligibility` | Public domain; cite the [TrialGPT paper](https://www.nature.com/articles/s41467-024-53081-z) | Download through a script or documented command; record source revision and SHA-256 |
| [TrialGPT code and preprocessed data](https://github.com/ncbi-nlp/TrialGPT) | Reference retrieval, matching and ranking implementation | Public code and TREC/SIGIR adapters are available | NCBI public-domain notice; preserve citation and disclaimer | Do not vendor the full repository; record the commit used |
| [TREC Clinical Trials 2021](https://trec.nist.gov/data/trials2021.html) | Trial-level ranking and eligible/excluded evaluation | qrels labels: 0 non-relevant, 1 excluded, 2 eligible | Cite TREC and follow corpus-specific download conditions | Use qrels with the corresponding 2021 corpus, not current API records. Do not treat unjudged trials as verified negatives |
| [TREC Clinical Trials 2022](https://trec.nist.gov/data/trials2022.html) | Independent trial-level ranking evaluation | qrels use the same 0/1/2 labels | Cite TREC and follow corpus-specific download conditions | Use the corresponding 2022 corpus and keep evaluation separate from live operational filters; official assessment ignored recruitment status and location |
| [Synthea](https://github.com/synthetichealth/synthea) | Optional structured EHR acquisition demo | Generates synthetic records in FHIR R4, STU3, DSTU2, Bulk FHIR, CSV and other formats | Apache-2.0 | Pin generator version and random seed; use as an acquisition-path demo, not the primary eligibility gold set |

## Derived masked-incomplete benchmark

This dataset does not exist as a ready-made public benchmark. It will be
derived from TrialGPT Criterion Annotations:

1. Select a patient note, criterion, expert label and expert evidence.
2. Mask the patient variable needed to judge that criterion.
3. Store the masked variable and its value as the expected patient answer.
4. Evaluate missing-variable detection and question generation on the masked note.
5. Add the answer and evaluate whether targeted re-evaluation recovers the
   original criterion label.

The generated artifact records its parent dataset revision, transformation
script version, row identifiers and SHA-256. The scale is chosen after the
adapter and first end-to-end run are working.

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

An exclusion criterion mapped to `met` must become
`blocks_eligibility` downstream. `not_applicable` remains distinct from
`unknown`; it maps to `neutral` in the deterministic rules.

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
