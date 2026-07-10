# ClarifyTrial Agent v1.2-final — Criterion State Transitions (locked)

Each criterion state starts uninitialized and is driven by the Criterion
Matching Agent, the clarification loop (global queue, session-level max 3
rounds), and the pure rules in `rules.py`.

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
    unknown --> review_required: still unknown after<br/>max 3 rounds,<br/>reason=max_rounds_exceeded

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

\* unknown becomes review_required with reason `max_rounds_exceeded` once
the session-level `clarification_round_count` reaches 3.

## Recommendation precedence (per trial, applied exactly in order)

1. any `eligibility_effect == blocks_eligibility` → **likely_ineligible**
2. else any `review_required == true` → **needs_human_review**
3. else uncertainty ratio above threshold → **uncertain**
4. else → **likely_eligible**

`trial_relevance_score` influences `ranking_score` only; it can never
override a hard eligibility block.
