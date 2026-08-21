from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from clarifytrial.pilots.trialgpt_metrics import compute_trialgpt_diagnostics


def record(
    criterion_type: str,
    expert_label: str,
    public_label: str,
    predicted_label: str,
    expert_evidence: list[int],
    predicted_evidence: list[int],
) -> dict[str, object]:
    return {
        "criterion_type": criterion_type,
        "expert_label": expert_label,
        "public_trialgpt_label": public_label,
        "predicted_label": predicted_label,
        "expert_evidence_ids": expert_evidence,
        "predicted_evidence_ids": predicted_evidence,
    }


def test_diagnostics_cover_labels_recovery_and_evidence_strata() -> None:
    diagnostics = compute_trialgpt_diagnostics(
        [
            record(
                "exclusion",
                "not excluded",
                "not excluded",
                "not enough information",
                [],
                [],
            ),
            record(
                "exclusion",
                "not excluded",
                "not enough information",
                "not excluded",
                [],
                [],
            ),
            record(
                "inclusion",
                "not enough information",
                "included",
                "not enough information",
                [1],
                [1],
            ),
            record(
                "inclusion",
                "included",
                "included",
                "included",
                [0, 1],
                [0],
            ),
            record(
                "exclusion",
                "not enough information",
                "not enough information",
                "not enough information",
                [],
                [2],
            ),
        ]
    )

    assert diagnostics["record_count"] == 5
    assert diagnostics["label_counts"]["expert"]["not excluded"] == 2
    assert diagnostics["label_counts"]["predicted"]["not enough information"] == 3
    assert diagnostics["confusion_matrices"]["predicted_vs_expert"]["not excluded"] == {
        "included": 0,
        "not included": 0,
        "excluded": 0,
        "not excluded": 1,
        "not enough information": 1,
        "not applicable": 0,
    }

    inclusion = diagnostics["accuracy_by_criterion_type"]["inclusion"]
    assert inclusion["predicted_vs_expert"]["accuracy"] == 1.0
    assert inclusion["public_trialgpt_vs_expert"]["accuracy"] == 0.5
    exclusion = diagnostics["accuracy_by_criterion_type"]["exclusion"]
    assert exclusion["predicted_vs_expert"]["accuracy"] == pytest.approx(2 / 3)
    assert exclusion["public_trialgpt_vs_expert"]["accuracy"] == pytest.approx(2 / 3)

    assert diagnostics["not_excluded"]["precision"] == 1.0
    assert diagnostics["not_excluded"]["recall"] == 0.5
    assert diagnostics["expert_nei"]["recall"] == 1.0
    assert diagnostics["public_nei_expert_decisive"] == {
        "total": 1,
        "recovered": 1,
        "recovery_rate": 1.0,
    }
    assert diagnostics["public_decisive_expert_nei"] == {
        "total": 1,
        "preserved": 1,
        "preservation_rate": 1.0,
    }
    assert diagnostics["evidence_exact"]["overall"]["exact_rate"] == 0.6
    assert diagnostics["evidence_exact"]["gold_empty"]["exact_rate"] == pytest.approx(
        2 / 3
    )
    assert diagnostics["evidence_exact"]["gold_nonempty"]["exact_rate"] == 0.5
    json.dumps(diagnostics)


@pytest.mark.parametrize(
    "invalid",
    [
        record("inclusion", "excluded", "included", "included", [], []),
        record("exclusion", "not excluded", "not excluded", "not excluded", [1, 1], []),
        {
            **record(
                "inclusion",
                "included",
                "included",
                "included",
                [],
                [],
            ),
            "hidden_gold": "must not be accepted",
        },
    ],
)
def test_invalid_normalized_records_are_rejected(invalid: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        compute_trialgpt_diagnostics([invalid])


def test_empty_diagnostics_have_explicit_undefined_rates() -> None:
    diagnostics = compute_trialgpt_diagnostics([])

    assert diagnostics["record_count"] == 0
    assert diagnostics["not_excluded"]["precision"] is None
    assert diagnostics["not_excluded"]["recall"] is None
    assert diagnostics["expert_nei"]["recall"] is None
    assert diagnostics["public_nei_expert_decisive"]["recovery_rate"] is None
    assert diagnostics["public_decisive_expert_nei"]["preservation_rate"] is None
    assert diagnostics["evidence_exact"]["gold_empty"]["exact_rate"] is None
    assert sum(
        sum(row.values())
        for row in diagnostics["confusion_matrices"]["predicted_vs_expert"].values()
    ) == 0
    json.dumps(diagnostics)
