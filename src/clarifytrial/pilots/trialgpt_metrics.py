"""Pure diagnostic metrics for TrialGPT criterion-level pilot records."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, field_validator, model_validator


CriterionType = Literal["inclusion", "exclusion"]
EligibilityLabel = Literal[
    "included",
    "not included",
    "excluded",
    "not excluded",
    "not enough information",
    "not applicable",
]

LABEL_ORDER: tuple[str, ...] = (
    "included",
    "not included",
    "excluded",
    "not excluded",
    "not enough information",
    "not applicable",
)
NEI = "not enough information"

_LABELS_BY_TYPE = {
    "inclusion": {"included", "not included", NEI, "not applicable"},
    "exclusion": {"excluded", "not excluded", NEI, "not applicable"},
}


class NormalizedCriterionRecord(BaseModel):
    """Minimal record accepted by :func:`compute_trialgpt_diagnostics`."""

    model_config = ConfigDict(extra="forbid", strict=True)

    criterion_type: CriterionType
    expert_label: EligibilityLabel
    public_trialgpt_label: EligibilityLabel
    predicted_label: EligibilityLabel
    expert_evidence_ids: list[int]
    predicted_evidence_ids: list[int]

    @field_validator("expert_evidence_ids", "predicted_evidence_ids")
    @classmethod
    def evidence_ids_are_unique_non_negative(cls, value: list[int]) -> list[int]:
        if any(identifier < 0 for identifier in value):
            raise ValueError("evidence identifiers must be non-negative")
        if len(value) != len(set(value)):
            raise ValueError("evidence identifiers must be unique")
        return value

    @model_validator(mode="after")
    def labels_match_criterion_type(self) -> Self:
        allowed = _LABELS_BY_TYPE[self.criterion_type]
        for field_name in (
            "expert_label",
            "public_trialgpt_label",
            "predicted_label",
        ):
            label = getattr(self, field_name)
            if label not in allowed:
                raise ValueError(
                    f"{field_name}={label!r} is invalid for {self.criterion_type}"
                )
        return self


def _ratio(numerator: int, denominator: int) -> float | None:
    return None if denominator == 0 else numerator / denominator


def _label_counts(
    records: list[NormalizedCriterionRecord],
    field_name: str,
) -> dict[str, int]:
    counts = Counter(getattr(record, field_name) for record in records)
    return {label: counts[label] for label in LABEL_ORDER}


def _confusion_matrix(
    records: list[NormalizedCriterionRecord],
    prediction_field: str,
) -> dict[str, dict[str, int]]:
    matrix = {
        expert_label: {predicted_label: 0 for predicted_label in LABEL_ORDER}
        for expert_label in LABEL_ORDER
    }
    for record in records:
        matrix[record.expert_label][getattr(record, prediction_field)] += 1
    return matrix


def _accuracy(
    records: list[NormalizedCriterionRecord],
    prediction_field: str,
) -> dict[str, int | float | None]:
    correct = sum(
        getattr(record, prediction_field) == record.expert_label
        for record in records
    )
    return {
        "total": len(records),
        "correct": correct,
        "accuracy": _ratio(correct, len(records)),
    }


def _evidence_exact(
    records: list[NormalizedCriterionRecord],
) -> dict[str, int | float | None]:
    exact = sum(
        set(record.predicted_evidence_ids) == set(record.expert_evidence_ids)
        for record in records
    )
    return {
        "total": len(records),
        "exact": exact,
        "exact_rate": _ratio(exact, len(records)),
    }


def compute_trialgpt_diagnostics(
    records: Iterable[NormalizedCriterionRecord | Mapping[str, Any]],
) -> dict[str, Any]:
    """Validate normalized records and return JSON-serializable diagnostics.

    Confusion-matrix rows are expert labels and columns are system predictions.
    The function has no filesystem, model, network, or global-state side effects.
    """

    normalized = [
        item
        if isinstance(item, NormalizedCriterionRecord)
        else NormalizedCriterionRecord.model_validate(item)
        for item in records
    ]

    by_type: dict[str, Any] = {}
    for criterion_type in ("inclusion", "exclusion"):
        subset = [
            record
            for record in normalized
            if record.criterion_type == criterion_type
        ]
        by_type[criterion_type] = {
            "predicted_vs_expert": _accuracy(subset, "predicted_label"),
            "public_trialgpt_vs_expert": _accuracy(
                subset,
                "public_trialgpt_label",
            ),
        }

    expert_not_excluded = [
        record for record in normalized if record.expert_label == "not excluded"
    ]
    predicted_not_excluded = [
        record for record in normalized if record.predicted_label == "not excluded"
    ]
    not_excluded_true_positive = sum(
        record.expert_label == "not excluded" for record in predicted_not_excluded
    )

    expert_nei = [record for record in normalized if record.expert_label == NEI]
    expert_nei_recovered = sum(record.predicted_label == NEI for record in expert_nei)

    public_nei_expert_decisive = [
        record
        for record in normalized
        if record.public_trialgpt_label == NEI and record.expert_label != NEI
    ]
    public_decisive_expert_nei = [
        record
        for record in normalized
        if record.public_trialgpt_label != NEI and record.expert_label == NEI
    ]

    gold_empty = [record for record in normalized if not record.expert_evidence_ids]
    gold_nonempty = [record for record in normalized if record.expert_evidence_ids]

    return {
        "record_count": len(normalized),
        "label_order": list(LABEL_ORDER),
        "confusion_matrix_axes": {
            "rows": "expert_label",
            "columns": "system_label",
        },
        "confusion_matrices": {
            "predicted_vs_expert": _confusion_matrix(
                normalized,
                "predicted_label",
            ),
            "public_trialgpt_vs_expert": _confusion_matrix(
                normalized,
                "public_trialgpt_label",
            ),
        },
        "label_counts": {
            "expert": _label_counts(normalized, "expert_label"),
            "predicted": _label_counts(normalized, "predicted_label"),
            "public_trialgpt": _label_counts(
                normalized,
                "public_trialgpt_label",
            ),
        },
        "accuracy_by_criterion_type": by_type,
        "not_excluded": {
            "true_positive": not_excluded_true_positive,
            "predicted_count": len(predicted_not_excluded),
            "expert_count": len(expert_not_excluded),
            "precision": _ratio(
                not_excluded_true_positive,
                len(predicted_not_excluded),
            ),
            "recall": _ratio(
                not_excluded_true_positive,
                len(expert_not_excluded),
            ),
        },
        "expert_nei": {
            "expert_count": len(expert_nei),
            "predicted_nei_count": expert_nei_recovered,
            "recall": _ratio(expert_nei_recovered, len(expert_nei)),
        },
        "public_nei_expert_decisive": {
            "total": len(public_nei_expert_decisive),
            "recovered": sum(
                record.predicted_label == record.expert_label
                for record in public_nei_expert_decisive
            ),
            "recovery_rate": _ratio(
                sum(
                    record.predicted_label == record.expert_label
                    for record in public_nei_expert_decisive
                ),
                len(public_nei_expert_decisive),
            ),
        },
        "public_decisive_expert_nei": {
            "total": len(public_decisive_expert_nei),
            "preserved": sum(
                record.predicted_label == NEI
                for record in public_decisive_expert_nei
            ),
            "preservation_rate": _ratio(
                sum(
                    record.predicted_label == NEI
                    for record in public_decisive_expert_nei
                ),
                len(public_decisive_expert_nei),
            ),
        },
        "evidence_exact": {
            "overall": _evidence_exact(normalized),
            "gold_empty": _evidence_exact(gold_empty),
            "gold_nonempty": _evidence_exact(gold_nonempty),
        },
    }
