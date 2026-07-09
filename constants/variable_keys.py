"""Single source of truth for canonical missing-variable keys.

All layers that emit or consume ``missing_variable_key`` / ``required_variables``
should canonicalize through this module.
"""

from __future__ import annotations

# Canonical keys (exactly 11; do not expand without an architecture revision).
ECOG_PERFORMANCE_STATUS = "ecog_performance_status"
RENAL_FUNCTION = "renal_function"
BIOMARKER_STATUS = "biomarker_status"
PRIOR_TREATMENT = "prior_treatment"
CURRENT_TREATMENT = "current_treatment"
DISEASE_STAGE = "disease_stage"
AGE = "age"
SEX = "sex"
HBA1C = "hba1c"
COMORBIDITIES = "comorbidities"
PREGNANCY_STATUS = "pregnancy_status"

CANONICAL_VARIABLE_KEYS: frozenset[str] = frozenset(
    {
        ECOG_PERFORMANCE_STATUS,
        RENAL_FUNCTION,
        BIOMARKER_STATUS,
        PRIOR_TREATMENT,
        CURRENT_TREATMENT,
        DISEASE_STAGE,
        AGE,
        SEX,
        HBA1C,
        COMORBIDITIES,
        PREGNANCY_STATUS,
    }
)

# Exhaustive alias map — no other aliases permitted.
VARIABLE_KEY_ALIASES: dict[str, str] = {
    "egfr_mutation_status": BIOMARKER_STATUS,
    "performance_status": ECOG_PERFORMANCE_STATUS,
    "creatinine": RENAL_FUNCTION,
}


def canonicalize_variable_key(key: str) -> str | None:
    """Return the canonical key, or ``None`` if *key* is not supported."""
    if key in CANONICAL_VARIABLE_KEYS:
        return key
    return VARIABLE_KEY_ALIASES.get(key)
