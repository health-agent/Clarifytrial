"""Evidence Context Builder Agent.

Responsibility: for each criterion, gather the patient-profile fields and
source sentences relevant to judging that criterion, producing an
``EvidenceContext``. Trial descriptions may be used for context/relevance
but must NOT introduce new blocking eligibility criteria unless the
protocol explicitly states them.
"""

from __future__ import annotations

from models import Criterion, EvidenceContext, PatientProfile, TrialContext


def build_evidence_context(
    criterion: Criterion,
    profile: PatientProfile,
    trial_context: TrialContext,
) -> EvidenceContext:
    """Collect evidence relevant to one criterion.

    TODO: retrieval/LLM step to select source sentences and profile fields
    matching criterion.required_variables.
    """
    raise NotImplementedError("Evidence building not implemented in skeleton")
