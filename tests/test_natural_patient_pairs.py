from __future__ import annotations

import json
from pathlib import Path

import pytest

from clarifytrial.datasets import (
    audit_natural_evaluation_patient_pairs,
    build_natural_evaluation_patient_pairs,
)


def _write(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _criterion(
    trial_id: str,
    criterion_id: str,
    fact_code: str,
) -> dict[str, object]:
    return {
        "criterion_id": criterion_id,
        "group_id": "group_a",
        "nct_id": trial_id,
        "kind": "inclusion",
        "candidate_id": f"{criterion_id}:candidate",
        "source_text": f"{fact_code} must be present",
        "line_number": 1,
        "confidence": "high",
        "fact_code": fact_code,
        "fact_description": fact_code.replace("_", " "),
        "criterion_summary": f"{fact_code} is present",
        "expected_value": "true",
        "operator": None,
        "threshold": None,
        "unit": None,
    }


def test_patient_pairs_keep_values_and_candidate_statuses_fixed(
    tmp_path: Path,
) -> None:
    trial_set_path = tmp_path / "trial-set.json"
    trials = [
        {
            "group_id": "group_a",
            "nct_id": f"T{index}",
            "title": f"Trial {index}",
        }
        for index in range(1, 6)
    ]
    criteria = [
        _criterion(f"T{index}", f"C{index}", f"pivotal_{index}")
        for index in range(1, 6)
    ]
    criteria.append(_criterion("T1", "C6", "variable_fact"))
    _write(trial_set_path, {"trials": trials, "criteria": criteria})
    config_path = tmp_path / "generation.json"
    _write(
        config_path,
        {
            "protocol_id": "test-patient-pairs",
            "profile_count_per_group": 2,
            "as_of": "2026-08-21T12:00:00+00:00",
            "fact_aliases": {},
            "groups": [
                {
                    "group_id": "group_a",
                    "development_profile_count": 1,
                    "pivotal_values": {
                        "pivotal_1": 1,
                        "pivotal_2": 1,
                        "pivotal_3": 1,
                        "pivotal_4": 1,
                        "pivotal_5": 1,
                    },
                }
            ],
        },
    )
    output = tmp_path / "patients.json"

    result = build_natural_evaluation_patient_pairs(
        trial_set_path=trial_set_path,
        generation_config_path=config_path,
        output_path=output,
    )

    assert result["patient_count"] == 2
    assert result["episode_count"] == 4
    assert result["development_patient_count"] == 1
    assert result["heldout_patient_count"] == 1
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["medical_data_notice"].startswith("All patient records")
    assert payload["medical_disclaimer"] == "학생 과제용 실험 결과입니다."
    for pair in payload["pairs"]:
        assert pair["expected_pair_relation"]["same_clinical_values"] is True
        assert pair["expected_pair_relation"]["same_candidate_statuses"] is True
        assert (
            pair["expected_pair_relation"][
                "verification_recovers_sufficient_decisions"
            ]
            is True
        )
        assert pair["expected_pair_relation"]["confirmation_changed_trial_ids"]
        assert len(pair["insufficient_evidence_episode"]["missing_information"]) == 5
        assert len(pair["insufficient_evidence_episode"]["verification_answers"]) == 5
    audit = audit_natural_evaluation_patient_pairs(
        trial_set_path=trial_set_path,
        generation_config_path=config_path,
        patient_pairs_path=output,
    )
    assert audit["passed"] is True
    assert audit["candidate_status_mismatch_count"] == 0
    assert audit["verification_recovery_mismatch_count"] == 0
    with pytest.raises(FileExistsError, match="already exists"):
        build_natural_evaluation_patient_pairs(
            trial_set_path=trial_set_path,
            generation_config_path=config_path,
            output_path=output,
        )
