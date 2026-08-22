import json

from clarifytrial.datasets.natural_records import (
    audit_natural_evaluation_records,
    build_natural_evaluation_records,
)


def _pairs(tmp_path):
    source = {
        "authority": "synthetic test",
        "medical_disclaimer": "research only",
        "pairs": [
            {
                "patient_id": "natural-demo-01",
                "group_id": "demo",
                "split": "development",
                "trial_ids": ["NCT1"],
                "pivotal_fact_codes": ["age"],
                "sufficient_evidence_episode": {
                    "episode_id": "natural-demo-01:sufficient",
                    "evidence": [
                        {
                            "evidence_id": "verified-age",
                            "statement": "합성 환자 Age in years: 55 years",
                            "source_type": "medical_record",
                            "source_location": "synthetic#age",
                            "event_date": "2026-08-19",
                            "recorded_date": "2026-08-20",
                            "verification_status": "verified",
                            "concept": "demo:age",
                            "value": 55,
                            "unit": "years",
                        }
                    ],
                    "expected_trial_decisions": [],
                },
                "insufficient_evidence_episode": {
                    "episode_id": "natural-demo-01:insufficient",
                    "evidence": [
                        {
                            "evidence_id": "reported-age",
                            "statement": "합성 환자 Age in years: 55 years",
                            "source_type": "patient_report",
                            "source_location": "synthetic#age",
                            "event_date": "2026-08-19",
                            "recorded_date": "2026-08-20",
                            "verification_status": "reported",
                            "concept": "demo:age",
                            "value": 55,
                            "unit": "years",
                        }
                    ],
                    "expected_trial_decisions": [],
                },
            }
        ],
    }
    path = tmp_path / "pairs.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    return path


def test_natural_records_preserve_values_and_expose_evidence_state(tmp_path):
    pairs = _pairs(tmp_path)
    output = tmp_path / "records.json"

    summary = build_natural_evaluation_records(
        patient_pairs_path=pairs, destination=output
    )
    audit = audit_natural_evaluation_records(
        patient_pairs_path=pairs, records_path=output
    )
    document = json.loads(output.read_text(encoding="utf-8"))

    assert summary["record_count"] == 2
    assert audit["passed"] is True
    assert "verified medical record" in document["records"][0]["record_text"]
    assert "not yet checked" in document["records"][1]["record_text"]
    assert document["records"][0]["expected_facts"][0]["value"] == 55
    assert document["records"][1]["expected_facts"][0]["value"] == 55
