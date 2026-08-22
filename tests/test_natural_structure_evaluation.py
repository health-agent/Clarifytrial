from clarifytrial.datasets.natural_structure_evaluation import (
    ExtractedMeasurement,
    ExtractedNaturalRecord,
    score_extracted_natural_record,
)


def _record():
    return {
        "expected_facts": [
            {
                "measurement_id": "age|year",
                "fact_code": "age",
                "value": 55,
                "unit": "years",
                "source_type": "medical_record",
                "verification_status": "verified",
            },
            {
                "measurement_id": "english|bool",
                "fact_code": "english",
                "value": 1,
                "unit": "bool",
                "source_type": "patient_report",
                "verification_status": "pending",
            },
        ],
        "pivotal_fact_codes": ["age", "english"],
    }


def test_structure_score_uses_values_and_evidence_state_not_text_offsets():
    output = ExtractedNaturalRecord(
        facts=[
            ExtractedMeasurement(
                measurement_id="age|year",
                value=55,
                unit="year",
                source_type="medical_record",
                verification_status="verified",
            ),
            ExtractedMeasurement(
                measurement_id="english|bool",
                value=1,
                unit="bool",
                source_type="patient_report",
                verification_status="pending",
            ),
        ]
    )

    score = score_extracted_natural_record(record=_record(), output=output)

    assert score["critical_fully_correct_rate"] == 1
    assert score["exact_record_match"] is True


def test_structure_score_separates_value_from_evidence_state_errors():
    output = ExtractedNaturalRecord(
        facts=[
            ExtractedMeasurement(
                measurement_id="age|year",
                value=55,
                unit="years",
                source_type="patient_report",
                verification_status="reported",
            )
        ]
    )

    score = score_extracted_natural_record(record=_record(), output=output)
    age = score["per_fact"][0]

    assert age["value_correct"] is True
    assert age["source_type_correct"] is False
    assert score["fact_recall"] == 0.5
    assert score["exact_record_match"] is False
