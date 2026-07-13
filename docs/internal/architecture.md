# ClarifyTrial Agent v1.2-final — Architecture (locked)

ClarifyTrial Agent is a **shared-state multi-agent system** for Interactive
Clinical Trial Recommendation. The **Eligibility State Tracker** owns the
central shared state (`PatientSession`), which is session-level and keyed by
`patient_id`. Multiple trials are stored under `trial_states_by_trial_id`;
each trial has its own `trial_context` and `criterion_states`. Missing
variables are deduplicated **globally** by `missing_variable_key`, and
clarification questions live in a single **global clarification queue**
(never per trial). `clarification_round_count` is session-level and has no
fixed upper bound. `trial_relevance_score` affects ranking only, never hard
eligibility.

```mermaid
flowchart TB
    subgraph input["Input Layer"]
        RAWTRIAL["Trial protocols (source-agnostic; future ClinicalTrials.gov API v2 adapter)"]
        RAWPATIENT["Patient free text / answers"]
    end

    subgraph agents["LLM Agents"]
        CP["Criteria Parser"]
        PPU["Patient Profile Understanding<br/>(also normalizes free-text answers)"]
        ECB["Evidence Context Builder"]
        CM["Criterion Matching"]
        MID["Missing Information Detection"]
        CQ["Clarification Question"]
        AUR["Answer Update & Targeted Re-evaluation"]
        TR["Trial Recommendation"]
        RE["Result Explanation"]
    end

    subgraph rulesmod["Rule / Schema Modules"]
        RULES["rules.py (pure rule functions:<br/>effect mapping, dedup, precedence, ranking)"]
        MODELS["models.py (Pydantic v2 schemas)"]
    end

    subgraph tracker["Eligibility State Tracker (central shared state)"]
        SESSION["PatientSession (session-level, keyed by patient_id)"]
        TSB["trial_states_by_trial_id<br/>(per trial: trial_context + criterion_states)"]
        GMVP["global_missing_variable_pool<br/>(deduplicated by missing_variable_key)"]
        GCQ["global_clarification_queue<br/>(global, no fixed round cap)"]
        SESSION --> TSB
        SESSION --> GMVP
        SESSION --> GCQ
    end

    subgraph output["Output Layer"]
        RECS["Ranked TrialRecommendations"]
        EXPL["FinalOutput (explanations + pending questions)"]
    end

    subgraph obs["Observability Layer"]
        LOGS["RequestLog (per agent action)"]
    end

    RAWTRIAL --> CP --> SESSION
    RAWPATIENT --> PPU --> SESSION
    SESSION --> ECB --> CM --> SESSION
    SESSION --> MID --> GMVP
    GMVP --> CQ --> GCQ
    GCQ --> RAWPATIENT
    PPU -->|normalized answer| AUR --> SESSION
    SESSION --> TR --> RECS
    RECS --> RE --> EXPL
    RULES -.-> CM
    RULES -.-> MID
    RULES -.-> TR
    MODELS -.-> SESSION
    agents -.-> LOGS
```

Notes on locked invariants:

- Free-text clarification answers always pass through the Patient Profile
  Understanding Agent for normalization **before** any rule update.
- Trial descriptions (`trial_context`) support context/relevance only; they
  must not create new blocking eligibility criteria unless explicitly stated
  in the protocol.
- The recommendation precedence and rule mappings are pure functions in
  `rules.py`; LLM agents never decide eligibility effects directly.
