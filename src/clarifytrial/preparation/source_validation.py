"""Public facade for natural-source matching and structured-value checks."""

from .source_matching import (
    SourceSpan,
    SourceValidationError,
    comparison_text,
    resolve_source_span,
)
from .structured_value_validation import (
    remove_unsupported_evidence_requirements,
    remove_unwritten_equality_constraint,
    validate_patient_fact_source,
    validate_trial_criterion_source,
)

__all__ = [
    "SourceSpan",
    "SourceValidationError",
    "comparison_text",
    "resolve_source_span",
    "remove_unsupported_evidence_requirements",
    "remove_unwritten_equality_constraint",
    "validate_patient_fact_source",
    "validate_trial_criterion_source",
]
