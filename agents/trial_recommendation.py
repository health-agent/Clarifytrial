"""Trial Recommendation Agent.

Responsibility: compute per-trial recommendations from criterion states
using the pure rules (recommendation precedence is locked), then rank
trials. ``trial_relevance_score`` affects ranking_score only — it can
never override a hard eligibility block.
"""

from __future__ import annotations

from models import PatientSession, TrialRecommendation
from rules import compute_trial_recommendation, rank_trials


def recommend_trials(session: PatientSession) -> list[TrialRecommendation]:
    """Compute and rank recommendations for every trial in the session.

    TODO: (optional LLM step) estimate trial_relevance_score from
    trial_context vs. patient profile; the recommendation itself remains
    purely rule-based.
    """
    recommendations = [
        compute_trial_recommendation(
            trial_id=trial_id,
            criterion_states=state.criterion_states,
            trial_relevance_score=state.trial_relevance_score,
        )
        for trial_id, state in session.trial_states_by_trial_id.items()
    ]
    return rank_trials(recommendations)
