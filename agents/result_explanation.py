"""Result Explanation Agent.

Responsibility: produce patient-friendly explanations of the final
recommendations (why a trial is likely eligible/ineligible, what is still
unknown, which questions remain), assembling the ``FinalOutput``. This is
presentation only; it never alters eligibility state or recommendations.
"""

from __future__ import annotations

from models import FinalOutput, PatientSession


def explain_results(session: PatientSession) -> FinalOutput:
    """Build the user-facing final output.

    TODO: LLM call to generate plain-language explanations per trial from
    blocking/supporting/uncertain criteria and pending questions.
    """
    raise NotImplementedError("Explanation generation not implemented in skeleton")
