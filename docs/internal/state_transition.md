# ClarifyTrial Agent v1.2-final — Criterion State Transitions (locked)

Each criterion state starts uninitialized and is driven by the Criterion
Matching Agent, the global clarification loop, and the pure rules in
`rules.py`. The loop has no fixed round cap by default.

```mermaid
stateDiagram-v2
    [*] --> uninitialized

    uninitialized --> met: matching
    uninitialized --> unmet: matching
    uninitialized --> unknown: matching
    uninitialized --> conflict: matching
    uninitialized --> not_applicable: matching

    state "unknown" as unknown
    unknown --> missing_info_detected: Missing Information Detection<br/>(missing_variable_key, deduplicated globally)
    missing_info_detected --> clarification_question: Clarification Question Agent<br/>(global clarification queue)
    clarification_question --> answer_update: patient answers<br/>(normalized by Patient Profile Understanding)
    answer_update --> targeted_reevaluation: Answer Update &<br/>Targeted Re-evaluation
    targeted_reevaluation --> met
    targeted_reevaluation --> unmet
    targeted_reevaluation --> unknown
    targeted_reevaluation --> conflict

    conflict --> review_required: effect=uncertain,<br/>reason=conflicting_evidence
    unknown --> review_required: controller stops clarification<br/>with unresolved evidence

    met --> [*]
    unmet --> [*]
    not_applicable --> [*]
    review_required --> [*]
```

## Effect mapping (per criterion)

| criterion_type | match status   | eligibility_effect   | review |
|----------------|----------------|----------------------|--------|
| inclusion      | met            | supports_eligibility | no     |
| inclusion      | unmet          | blocks_eligibility   | no     |
| inclusion      | unknown        | uncertain            | no*    |
| exclusion      | met            | blocks_eligibility   | no     |
| exclusion      | unmet          | supports_eligibility | no     |
| exclusion      | unknown        | uncertain            | no*    |
| any            | conflict       | uncertain            | yes (conflicting_evidence) |
| any            | not_applicable | neutral              | no     |

\* unknown remains uncertain during clarification. A configured stopping
policy may route unresolved criteria to review.

## Recommendation precedence (per trial, applied exactly in order)

1. any `eligibility_effect == blocks_eligibility` → **likely_ineligible**
2. else any `review_required == true` → **needs_human_review**
3. else uncertainty ratio above threshold → **uncertain**
4. else → **likely_eligible**

`trial_relevance_score` influences `ranking_score` only; it can never
override a hard eligibility block.
