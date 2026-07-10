"""Eligibility State Tracker Agent.

Responsibility: own the CENTRAL SHARED STATE of the system, the
``PatientSession``. State is session-level and keyed by ``patient_id``.
Multiple trials live under ``trial_states_by_trial_id``; each trial keeps
its own ``trial_context`` and ``criterion_states``. This agent is the only
writer of session state; all other agents read from it and propose updates
through it.

It also enforces the session-level ``clarification_round_count`` cap
(max 3) and applies the pure rule functions from ``rules.py`` when
criterion match statuses change.
"""

from __future__ import annotations

from models import (
    CriterionMatchStatus,
    CriterionState,
    PatientProfile,
    PatientSession,
    TrialState,
)


def create_session(profile: PatientProfile) -> PatientSession:
    """Initialize an empty session keyed by the patient id."""
    return PatientSession(patient_id=profile.patient_id, patient_profile=profile)


def register_trial(session: PatientSession, trial_state: TrialState) -> PatientSession:
    """Add a trial's state slice under trial_states_by_trial_id.

    TODO: idempotency checks and criterion-state initialization from parsed
    criteria.
    """
    raise NotImplementedError("Trial registration not implemented in skeleton")


def update_criterion_status(
    session: PatientSession,
    trial_id: str,
    criterion_id: str,
    new_status: CriterionMatchStatus,
) -> CriterionState:
    """Update one criterion's match status and re-derive its effect.

    TODO: apply rules.derive_eligibility_effect (passing
    max_rounds_exceeded=session.clarification_round_count >= 3 when the
    status remains unknown) and persist the updated CriterionState.
    """
    raise NotImplementedError("State update not implemented in skeleton")


def increment_clarification_round(session: PatientSession) -> PatientSession:
    """Increment the session-level round counter, capped at 3.

    TODO: raise/flag when the cap is reached so unknown criteria are routed
    to review (max_rounds_exceeded).
    """
    raise NotImplementedError("Round tracking not implemented in skeleton")
