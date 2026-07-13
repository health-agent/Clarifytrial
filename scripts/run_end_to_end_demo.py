"""Deterministic end-to-end dry-run demo for ClarifyTrial Agent.

Runs the full state flow of the locked v1.2-final architecture on
synthetic data with NO LLM or external API calls: mock criterion match
statuses stand in for LLM output, and everything downstream (eligibility
effects, global missing-variable dedup, clarification queue,
recommendation precedence, ranking) is computed by the real pure rules
in ``rules.py`` over the real Pydantic models.

Usage (from any cwd):

    python scripts/run_end_to_end_demo.py

Writes outputs/end_to_end_demo_summary.md. Synthetic dry run only — not
medical advice, no claim of medical accuracy.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from agents.patient_profile_understanding import (  # noqa: E402
    extract_patient_profile_from_summary,
)
from models import (  # noqa: E402
    CriterionMatchStatus,
    CriterionState,
    CriterionType,
    FinalOutput,
    FollowUpQuestion,
    PatientSession,
    QuestionStatus,
    SyntheticPatientDataset,
    SyntheticTrialProtocolDataset,
    TrialContext,
    TrialState,
)
from rules import (  # noqa: E402
    compute_trial_recommendation,
    deduplicate_missing_variables,
    derive_eligibility_effect,
    rank_trials,
)

EXAMPLES_DIR = PROJECT_ROOT / "examples"
OUTPUTS_DIR = PROJECT_ROOT / "outputs"
SUMMARY_PATH = OUTPUTS_DIR / "end_to_end_demo_summary.md"

BANNER = "[SYNTHETIC DRY RUN]"


def make_state(
    criterion_id: str,
    trial_id: str,
    criterion_type: CriterionType,
    status: CriterionMatchStatus,
    missing_variable_keys: list[str] | None = None,
) -> CriterionState:
    """Build a mock criterion state and derive its effect via the real rules.

    The match status itself is what an LLM Criterion Matching Agent would
    produce; here it is mocked deterministically.
    """
    effect, review_required, review_reason = derive_eligibility_effect(
        criterion_type, status
    )
    return CriterionState(
        criterion_id=criterion_id,
        trial_id=trial_id,
        criterion_type=criterion_type,
        criterion_match_status=status,
        eligibility_effect=effect,
        review_required=review_required,
        review_reason=review_reason,
        missing_variable_keys=missing_variable_keys or [],
    )


def main() -> FinalOutput:
    log: list[str] = []

    def stage(msg: str) -> None:
        line = f"{BANNER} {msg}"
        print(line)
        log.append(msg)

    # Stage 1-2: load synthetic inputs.
    patients = SyntheticPatientDataset.model_validate(
        json.loads((EXAMPLES_DIR / "synthetic_patients.json").read_text(encoding="utf-8"))
    )
    protocols = SyntheticTrialProtocolDataset.model_validate(
        json.loads(
            (EXAMPLES_DIR / "synthetic_trial_protocols.json").read_text(encoding="utf-8")
        )
    )
    stage(
        f"Stage 1-2: loaded {len(patients.topics)} synthetic patient summaries "
        f"and {len(protocols.trials)} mock trial protocols (no network, no LLM)."
    )

    # Stage 3: select one patient and two trials.
    case = patients.topics[0]  # S001, synthetic NSCLC vignette
    trials = [t for t in protocols.trials if t.trial_id in ("MOCK-TRIAL-001", "MOCK-TRIAL-002")]
    stage(
        f"Stage 3: selected patient {case.num} and trials "
        f"{', '.join(t.trial_id for t in trials)}."
    )

    # Stage 4: create the central shared state (PatientSession).
    profile = extract_patient_profile_from_summary(case.num, case.title)
    session = PatientSession(patient_id=profile.patient_id, patient_profile=profile)
    stage(
        f"Stage 4: created PatientSession keyed by patient_id={session.patient_id} "
        "(central shared state, Eligibility State Tracker)."
    )

    # Stage 5-6: mock criterion match statuses (stand-in for the LLM
    # Criterion Matching Agent) and derive effects via the real rules.
    trial_states: dict[str, list[CriterionState]] = {
        "MOCK-TRIAL-001": [
            # met inclusion (age >= 18)
            make_state("MOCK-TRIAL-001-INC-01", "MOCK-TRIAL-001",
                       CriterionType.inclusion, CriterionMatchStatus.met),
            # unknown inclusion with a missing variable (ECOG)
            make_state("MOCK-TRIAL-001-INC-03", "MOCK-TRIAL-001",
                       CriterionType.inclusion, CriterionMatchStatus.unknown,
                       ["ecog_performance_status"]),
            # conflicting evidence on an exclusion (brain metastases)
            make_state("MOCK-TRIAL-001-EXC-02", "MOCK-TRIAL-001",
                       CriterionType.exclusion, CriterionMatchStatus.conflict),
        ],
        "MOCK-TRIAL-002": [
            # met inclusion (age >= 18)
            make_state("MOCK-TRIAL-002-INC-01", "MOCK-TRIAL-002",
                       CriterionType.inclusion, CriterionMatchStatus.met),
            # unknown inclusion sharing the SAME missing variable (dedup demo)
            make_state("MOCK-TRIAL-002-INC-03", "MOCK-TRIAL-002",
                       CriterionType.inclusion, CriterionMatchStatus.unknown,
                       ["ecog_performance_status"]),
            # unmet inclusion (renal function) -> blocks
            make_state("MOCK-TRIAL-002-INC-04", "MOCK-TRIAL-002",
                       CriterionType.inclusion, CriterionMatchStatus.unmet),
            # met exclusion (prior platinum chemo) -> blocks
            make_state("MOCK-TRIAL-002-EXC-01", "MOCK-TRIAL-002",
                       CriterionType.exclusion, CriterionMatchStatus.met),
        ],
    }
    relevance = {"MOCK-TRIAL-001": 0.8, "MOCK-TRIAL-002": 0.6}
    for trial in trials:
        session.trial_states_by_trial_id[trial.trial_id] = TrialState(
            trial_id=trial.trial_id,
            trial_context=TrialContext(
                trial_id=trial.trial_id,
                description=trial.trial_description or "",
                conditions=trial.conditions,
                interventions=trial.interventions,
            ),
            criterion_states=trial_states[trial.trial_id],
            trial_relevance_score=relevance[trial.trial_id],
        )
    all_states = [
        s for ts in session.trial_states_by_trial_id.values() for s in ts.criterion_states
    ]
    stage(
        f"Stage 5-6: mocked {len(all_states)} criterion match statuses "
        "(met/unmet/unknown/conflict) and derived eligibility effects with "
        "rules.derive_eligibility_effect."
    )

    # Stage 7: global missing-variable deduplication.
    session.global_missing_variable_pool = deduplicate_missing_variables(all_states)
    for key, item in session.global_missing_variable_pool.items():
        stage(
            f"Stage 7: missing variable '{key}' deduplicated globally -- one pool "
            f"item covering criteria {item.affected_criterion_ids} across trials "
            f"{item.affected_trial_ids}."
        )

    # Stage 8: global clarification queue (one question per pool item).
    session.global_clarification_queue = [
        FollowUpQuestion(
            question_id=f"q-{i + 1:03d}",
            missing_variable_key=item.missing_variable_key,
            target_profile_field=f"variables.{item.missing_variable_key}",
            expected_answer_type="integer",
            allowed_values_or_schema=[0, 1, 2, 3, 4],
            affected_criterion_ids=item.affected_criterion_ids,
            question_text=(
                "How would you rate your current level of daily activity? "
                "(ECOG performance status, 0 = fully active to 4 = bedridden) "
                "[synthetic demo question]"
            ),
            priority_rank=i + 1,
            status=QuestionStatus.pending,
        )
        for i, item in enumerate(session.global_missing_variable_pool.values())
    ]
    session.clarification_round_count = 1
    stage(
        f"Stage 8: queued {len(session.global_clarification_queue)} clarification "
        "question(s) in the GLOBAL queue (round 1)."
    )

    # Stage 9: recommendations via the real precedence rules + ranking.
    recommendations = [
        compute_trial_recommendation(
            trial_id=tid,
            criterion_states=ts.criterion_states,
            trial_relevance_score=ts.trial_relevance_score,
        )
        for tid, ts in session.trial_states_by_trial_id.items()
    ]
    session.trial_recommendations = rank_trials(recommendations)
    for rec in session.trial_recommendations:
        stage(
            f"Stage 9: rank {rec.rank}: {rec.trial_id} -> {rec.recommendation.value} "
            f"(blocking={rec.blocking_criteria or 'none'}, "
            f"ranking_score={rec.ranking_score:.2f})."
        )

    # Stage 10: final output (existing FinalOutput model fits).
    explanations = {}
    for rec in session.trial_recommendations:
        parts = [f"Recommendation: {rec.recommendation.value} (rank {rec.rank})."]
        if rec.blocking_criteria:
            parts.append(f"Blocking criteria: {', '.join(rec.blocking_criteria)}.")
        if rec.uncertain_criteria:
            parts.append(f"Uncertain criteria: {', '.join(rec.uncertain_criteria)}.")
        if rec.pending_questions:
            parts.append(f"Pending variables: {', '.join(rec.pending_questions)}.")
        explanations[rec.trial_id] = " ".join(parts)
    final_output = FinalOutput(
        patient_id=session.patient_id,
        trial_recommendations=session.trial_recommendations,
        explanations=explanations,
        pending_questions=session.global_clarification_queue,
    )
    stage("Stage 10: assembled FinalOutput (recommendations + explanations + pending questions).")

    # Stage 11: write the markdown summary.
    OUTPUTS_DIR.mkdir(exist_ok=True)
    lines = [
        "# End-to-End Dry-Run Demo Summary (SYNTHETIC)",
        "",
        "Deterministic dry run over synthetic data — no LLM calls, no external",
        "APIs, no real patient data. Mock criterion match statuses stand in for",
        "LLM output; all decisions come from the pure rules in `rules.py`.",
        "**Not medical advice; no claim of medical accuracy.**",
        "",
        f"- Patient: `{session.patient_id}` (synthetic case summary input)",
        f"- Trials evaluated: {', '.join(session.trial_states_by_trial_id)}",
        f"- Criterion states mocked: {len(all_states)}",
        f"- Global missing variables (deduplicated): "
        f"{', '.join(session.global_missing_variable_pool) or 'none'}",
        f"- Clarification questions queued (global, round "
        f"{session.clarification_round_count}): "
        f"{len(session.global_clarification_queue)}",
        "",
        "## Ranked recommendations",
        "",
        "| Rank | Trial | Recommendation | Blocking criteria | Pending variables |",
        "|---|---|---|---|---|",
    ]
    for rec in session.trial_recommendations:
        lines.append(
            f"| {rec.rank} | {rec.trial_id} | {rec.recommendation.value} "
            f"| {', '.join(rec.blocking_criteria) or '—'} "
            f"| {', '.join(rec.pending_questions) or '—'} |"
        )
    lines += ["", "## Stage log", ""]
    lines += [f"{i + 1}. {msg}" for i, msg in enumerate(log)]
    lines += ["", "## Medical disclaimer", "", final_output.medical_disclaimer]
    lines.append("")
    SUMMARY_PATH.write_text("\n".join(lines), encoding="utf-8")
    stage(f"Stage 11: wrote {SUMMARY_PATH.relative_to(PROJECT_ROOT)}.")

    return final_output


if __name__ == "__main__":
    main()
