"""Public facade for natural-source matching and structured-value checks."""

from .source_matching import (
    SourceSpan,
    SourceValidationError,
    comparison_text,
    resolve_source_span,
)
from .structured_value_validation import (
    validate_patient_fact_source,
    validate_trial_criterion_source,
)

__all__ = [
    "SourceSpan",
    "SourceValidationError",
    "comparison_text",
    "resolve_source_span",
    "validate_patient_fact_source",
    "validate_trial_criterion_source",
]
