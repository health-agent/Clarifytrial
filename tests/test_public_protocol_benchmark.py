from __future__ import annotations

import json
from pathlib import Path

from clarifytrial.app.evaluation import run_full_workflow_evaluation
from clarifytrial.datasets.source_criteria import (
    ExplicitLogicGroup,
    plausible_numeric_range,
    structure_trial_criteria,
)
from clarifytrial.llm import DeterministicWorkflowModel
from clarifytrial.preparation.team_trials import TeamTrialRecord
from clarifytrial.ui import build_integrated_ui_fixture


ROOT = Path(__file__).resolve().parents[1]
TRIAL_SET = ROOT / "data/public_protocol_benchmark_v1/trial_set.json"
PATIENT_PAIRS = ROOT / "data/public_protocol_benchmark_v1/patient_pairs.json"
GENERATION_CONFIG = ROOT / "configs/natural_evaluation_patient_generation_v2.json"


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_public_protocol_subset_keeps_sources_logic_and_patient_burden() -> None:
    trials = _read(TRIAL_SET)
    patients = _read(PATIENT_PAIRS)

    assert trials["status"] == "public_protocol_derived_benchmark"
    assert trials["trial_count"] == 50
    assert trials["criterion_count"] == 202
    assert trials["explicit_non_all_logic_trial_count"] == 3
    assert trials["explicit_non_all_logic_group_count"] == 4
    serialized_logic = json.dumps(
        [item["eligibility_logic"] for item in trials["trials"]]
    )
    assert '"operator": "any"' in serialized_logic
    assert '"operator": "at_least"' in serialized_logic
    assert trials["source_snapshot"]["sha256"]
    assert all(
        item["source_location"].startswith("https://clinicaltrials.gov/study/")
        for item in trials["criteria"]
    )
    assert patients["patient_count"] == 50
    assert patients["as_of"] == "2026-08-25T09:00:00+09:00"
    assert set(patients["acquisition_mode_counts"]) == {
        "existing_official_result",
        "internal_record",
        "new_noninvasive_test",
        "patient_report",
    }


def test_explicit_two_of_three_source_block_is_not_flattened_to_all() -> None:
    record = TeamTrialRecord(
        nct_id="NCT-LOGIC",
        title="Structured source logic",
        conditions=["test condition"],
        minimum_age="18 Years",
        maximum_age="80 Years",
        overall_status="RECRUITING",
        eligibility_text=(
            "Inclusion Criteria:\n"
            "* ECOG >= 0\n"
            "* abdominal pain\n"
            "* serum marker elevation\n"
            "* imaging finding\n"
            "Exclusion Criteria:\n"
            "* Active severe infection"
        ),
    )
    declaration = ExplicitLogicGroup(
        trial_id="NCT-LOGIC",
        label="세 항목 중 두 항목",
        operator="at_least",
        source_line_numbers=[3, 4, 5],
        minimum_required=2,
    )

    rows, logic, counts = structure_trial_criteria(
        record=record,
        group_id="test_group",
        maximum_criteria=8,
        minimum_criteria=2,
        logic_declarations=[declaration],
    )

    grouped = next(item for item in logic.children if item.operator.value == "at_least")
    assert grouped.minimum_required == 2
    assert len(grouped.children) == 3
    assert all(item.criterion_id for item in grouped.children)
    assert counts["declared_explicit_logic"] == 3
    assert {
        row["line_number"]
        for row in rows
        if row["confidence"] == "declared_explicit_logic"
    } == {3, 4, 5}


def test_compound_numeric_sentence_is_not_misread_as_an_ecog_cutoff() -> None:
    record = TeamTrialRecord(
        nct_id="NCT-COMPOUND",
        title="Compound numeric source line",
        conditions=["test condition"],
        minimum_age="18 Years",
        maximum_age=None,
        overall_status="RECRUITING",
        eligibility_text=(
            "Inclusion Criteria:\n"
            "13. Life expectancy <12 months and ECOG score of at least 2\n"
            "* ECOG <= 1\n"
            "* Prior platinum- and/or gemcitabine-based treatment"
        ),
    )

    rows, _, _ = structure_trial_criteria(
        record=record,
        group_id="test_group",
        maximum_criteria=5,
        minimum_criteria=2,
        logic_declarations=[],
    )

    assert all("Life expectancy" not in row["source_text"] for row in rows)
    assert all("and/or" not in row["source_text"] for row in rows)
    ecog = [row for row in rows if row["fact_code"].startswith("ecog")]
    assert [(row["operator"], row["threshold"]) for row in ecog] == [("lte", 1.0)]


def test_synthetic_numeric_values_stay_within_broad_human_ranges() -> None:
    patients = _read(PATIENT_PAIRS)
    values = [
        value
        for pair in patients["pairs"]
        for value in pair["clinical_values"]
    ]
    numeric_values = [item for item in values if item["unit"] != "bool"]

    assert numeric_values
    for item in numeric_values:
        bounds = plausible_numeric_range(item["fact_code"], item["unit"])
        assert bounds is not None
        assert bounds[0] <= item["value"] <= bounds[1]


def test_development_and_heldout_profiles_include_similar_outcome_difficulty() -> None:
    patients = _read(PATIENT_PAIRS)

    rates = {}
    for split in ("development", "heldout"):
        decisions = [
            decision
            for pair in patients["pairs"]
            if pair["split"] == split
            for decision in pair["sufficient_evidence_episode"][
                "expected_trial_decisions"
            ]
        ]
        rates[split] = sum(
            item["confirmation_status"] == "confirmed" for item in decisions
        ) / len(decisions)

    assert patients["synthetic_value_assignment"] == "declared_profile_value_order"
    assert abs(rates["development"] - rates["heldout"]) <= 0.10


def test_public_fixture_uses_dataset_time_and_normalizes_both_sides() -> None:
    fixture = build_integrated_ui_fixture(
        trial_set_path=TRIAL_SET,
        patient_pairs_path=PATIENT_PAIRS,
        generation_config_path=GENERATION_CONFIG,
        patient_id="source-acute_pancreatitis-04",
    )

    assert fixture.screening_case.initial_patient_state.as_of.isoformat() == (
        "2026-08-25T09:00:00+09:00"
    )
    age_answer = next(
        item
        for item in fixture.hidden_answers
        if item.fact_id.endswith(":age_years")
    )
    age_criteria = [
        criterion
        for trial in fixture.screening_case.trials
        for criterion in trial.criteria
        if criterion.numeric_constraint is not None
        and criterion.numeric_constraint.concept.endswith(":age")
    ]
    assert age_answer.evidence.concept == "acute_pancreatitis:age"
    assert age_criteria


def test_broad_search_and_downstream_decisions_use_the_same_patient(
    tmp_path: Path,
) -> None:
    trial_document = _read(TRIAL_SET)
    group_id = "acute_pancreatitis"
    group_trials = [
        item for item in trial_document["trials"] if item["group_id"] == group_id
    ]
    corpus = tmp_path / "trials.jsonl"
    rows = [
        {
            "nct_id": item["nct_id"],
            "title": item["title"],
            "conditions": item["conditions"],
            "brief_summary": item["title"],
            "eligibility_text": "Public eligibility criteria",
            "sex": "ALL",
            "minimum_age": "18 Years",
            "maximum_age": None,
            "overall_status": "RECRUITING",
            "phase": [],
        }
        for item in group_trials
    ]
    rows.append(
        {
            "nct_id": "NCT-DISTRACTOR",
            "title": "Unrelated healthy volunteer study",
            "conditions": ["healthy volunteers"],
            "brief_summary": "Unrelated study",
            "eligibility_text": "Public eligibility criteria",
            "sex": "ALL",
            "minimum_age": "18 Years",
            "maximum_age": None,
            "overall_status": "RECRUITING",
            "phase": [],
        }
    )
    corpus.write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in rows),
        encoding="utf-8",
    )

    result = run_full_workflow_evaluation(
        trial_set_path=TRIAL_SET,
        patient_pairs_path=PATIENT_PAIRS,
        generation_config_path=GENERATION_CONFIG,
        destination=tmp_path / "evaluation",
        model=DeterministicWorkflowModel(),
        model_label="deterministic-workflow",
        patient_ids=["source-acute_pancreatitis-04"],
        broad_corpus_path=corpus,
        broad_search_top_k=6,
        approve_synthetic_actions=True,
        progress=lambda _: None,
    )

    broad = result["broad_search_metrics"]
    assert broad["target_trial_count"] == 5
    assert broad["retrieved_target_count"] == 5
    assert broad["target_recall"] == 1.0
    assert result["evaluation_scope"]["includes_broad_corpus_search"] is True
