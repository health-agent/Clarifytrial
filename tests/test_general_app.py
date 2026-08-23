from __future__ import annotations

import json
from pathlib import Path

import pytest

from clarifytrial.app import GeneralRunOptions, ScreeningSession, run_general_screening
from clarifytrial.app.loaders import (
    load_general_patient,
    load_structured_trials,
    prepare_general_case,
)
from clarifytrial.app.tools import evidence_from_user_input
from clarifytrial.contracts import (
    EvidenceCaptureMethod,
    EvidenceSourceType,
    NextAction,
    VerificationStatus,
)
from clarifytrial.llm import DeterministicWorkflowModel
from clarifytrial.preparation import TrialProtocolSource
from clarifytrial.preparation.contracts import CandidateSearchHit
from clarifytrial.settings import EpisodeSettings


ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "examples" / "general_screening"


def _options(output: Path, *, resume: Path | None = None) -> GeneralRunOptions:
    return GeneralRunOptions(
        patient_path=EXAMPLE / "patient.json",
        trials_path=EXAMPLE / "trials.jsonl",
        answers_path=EXAMPLE / "answers.json" if resume is None else None,
        output_dir=output,
        resume_path=resume,
        settings=EpisodeSettings(
            max_external_actions=3,
            max_selective_reviews=1,
            max_cycles=12,
        ),
    )


def test_general_files_search_and_run_without_fixed_fixture(tmp_path: Path) -> None:
    patient = load_general_patient(EXAMPLE / "patient.json")
    trials = load_structured_trials(EXAMPLE / "trials.jsonl")
    prepared = prepare_general_case(patient, trials)

    assert prepared.trial_pool_count == 3
    assert [item.trial_id for item in prepared.case.trials] == [
        "NCT-SYNTH-A",
        "NCT-SYNTH-B",
    ]

    outcome = run_general_screening(
        options=_options(tmp_path),
        model=DeterministicWorkflowModel(),
        model_label="deterministic-workflow",
        medical_disclaimer="학생 과제용 실험 결과입니다.",
        write=lambda _: None,
    )

    assert outcome.paused is False
    result = json.loads(outcome.result_path.read_text(encoding="utf-8"))
    assert result["screening"]["stop_reason"] == "all_trials_resolved"
    assert result["usage"]["call_count"] == 3
    assert all(
        item["confirmation_status"] == "confirmed"
        for item in result["screening"]["final_decisions"]
    )
    provenance_by_id = {
        item["evidence_id"]: item["input_provenance"]["capture_method"]
        for item in result["screening"]["final_patient_state"]["facts"]
    }
    assert provenance_by_id["historical-hba1c"] == "imported_json_file"
    assert provenance_by_id["recent-official-hba1c"] == "synthetic_environment"


def test_general_search_accepts_a_new_condition_without_code_change(
    tmp_path: Path,
) -> None:
    patient = json.loads((EXAMPLE / "patient.json").read_text(encoding="utf-8"))
    patient["case_id"] = "new-condition-case"
    patient["search_conditions"] = ["rare synthetic condition"]
    patient_path = tmp_path / "patient.json"
    patient_path.write_text(json.dumps(patient), encoding="utf-8")
    rows = [json.loads(line) for line in (EXAMPLE / "trials.jsonl").read_text(encoding="utf-8").splitlines()]
    for row in rows[:2]:
        row["conditions"] = ["rare synthetic condition"]
        row["title"] = "Rare synthetic condition study"
    trial_path = tmp_path / "trials.jsonl"
    trial_path.write_text(
        "\n".join(json.dumps(item) for item in rows) + "\n",
        encoding="utf-8",
    )

    prepared = prepare_general_case(
        load_general_patient(patient_path),
        load_structured_trials(trial_path),
    )

    assert len(prepared.case.trials) == 2


def test_general_case_can_use_an_external_candidate_ranking() -> None:
    patient = load_general_patient(EXAMPLE / "patient.json")
    trials = load_structured_trials(EXAMPLE / "trials.jsonl")
    source_by_id = {item.trial_id: item for item in trials}

    class ExternalRanking:
        def search(self, search_conditions, *, top_k):
            assert search_conditions == ["type 2 diabetes"]
            assert top_k == 500
            return [
                CandidateSearchHit(
                    rank=1,
                    score=0.9,
                    retrieval_method="published-search-adapter",
                    source=TrialProtocolSource(
                        trial_id="NCT-SYNTH-B",
                        title=source_by_id["NCT-SYNTH-B"].title,
                        conditions=["type 2 diabetes"],
                        summary="",
                        eligibility_text="structured separately",
                        source_location="external-index#NCT-SYNTH-B",
                    ),
                ),
                CandidateSearchHit(
                    rank=2,
                    score=0.8,
                    retrieval_method="published-search-adapter",
                    source=TrialProtocolSource(
                        trial_id="NCT-SYNTH-A",
                        title=source_by_id["NCT-SYNTH-A"].title,
                        conditions=["type 2 diabetes"],
                        summary="",
                        eligibility_text="structured separately",
                        source_location="external-index#NCT-SYNTH-A",
                    ),
                ),
            ]

    prepared = prepare_general_case(
        patient,
        trials,
        candidate_search=ExternalRanking(),
        search_depth=500,
    )

    assert [item.trial_id for item in prepared.case.trials] == [
        "NCT-SYNTH-B",
        "NCT-SYNTH-A",
    ]
    assert prepared.candidate_hits[0].retrieval_method == "published-search-adapter"


def test_interactive_session_pauses_and_resumes(tmp_path: Path) -> None:
    first = GeneralRunOptions(
        patient_path=EXAMPLE / "patient.json",
        trials_path=EXAMPLE / "trials.jsonl",
        output_dir=tmp_path,
        settings=EpisodeSettings(
            max_external_actions=3,
            max_selective_reviews=1,
            max_cycles=12,
        ),
    )
    paused = run_general_screening(
        options=first,
        model=DeterministicWorkflowModel(),
        model_label="deterministic-workflow",
        medical_disclaimer="학생 과제용 실험 결과입니다.",
        read=lambda _: "quit",
        write=lambda _: None,
    )
    assert paused.paused is True
    session = ScreeningSession.model_validate_json(
        paused.session_path.read_text(encoding="utf-8")
    )
    assert session.completed is False
    assert session.action_count == 0
    legacy_session = session.model_dump(mode="json")
    legacy_session["format_version"] = 1
    legacy_session["metadata"].pop("candidate_trial_ids")
    legacy_session["metadata"].pop("candidate_search_method")
    paused.session_path.write_text(
        json.dumps(legacy_session),
        encoding="utf-8",
    )

    answer = json.dumps(
        {
            "statement": "Official HbA1c result was 6.4 percent.",
            "concept": "hba1c",
            "value": 6.4,
            "unit": "%",
            "event_date": "2026-08-20",
            "recorded_date": "2026-08-20",
            "source_type": "official_verification",
            "source_location": "synthetic-official-result#hba1c",
            "verification_status": "verified",
        }
    )
    resumed_options = GeneralRunOptions(
        patient_path=EXAMPLE / "patient.json",
        trials_path=EXAMPLE / "trials.jsonl",
        output_dir=tmp_path,
        settings=first.settings,
        resume_path=paused.session_path,
    )
    resumed = run_general_screening(
        options=resumed_options,
        model=DeterministicWorkflowModel(),
        model_label="deterministic-workflow",
        medical_disclaimer="학생 과제용 실험 결과입니다.",
        read=lambda _: answer,
        write=lambda _: None,
    )
    assert resumed.paused is False
    final_session = ScreeningSession.model_validate_json(
        resumed.session_path.read_text(encoding="utf-8")
    )
    assert final_session.completed is True
    assert final_session.format_version == 2
    assert final_session.action_count == 1
    assert final_session.revealed_fact_ids == ["recent-hba1c"]
    assert all(
        item.confirmation_status.value == "confirmed"
        for item in final_session.result.final_decisions
    )


def test_saved_session_resumes_after_patient_and_clinician_approval(
    tmp_path: Path,
) -> None:
    patient_document = json.loads(
        (EXAMPLE / "patient.json").read_text(encoding="utf-8")
    )
    option = patient_document["acquisition_options"][0]
    option.update(
        {
            "acquisition_mode": "new_noninvasive_test",
            "visit_required": True,
            "new_test_required": True,
            "requires_patient_choice": True,
            "requires_clinician_authorization": True,
        }
    )
    patient_path = tmp_path / "patient.json"
    patient_path.write_text(json.dumps(patient_document), encoding="utf-8")
    settings = EpisodeSettings(
        max_external_actions=3,
        max_selective_reviews=1,
        max_cycles=12,
    )
    first = run_general_screening(
        options=GeneralRunOptions(
            patient_path=patient_path,
            trials_path=EXAMPLE / "trials.jsonl",
            answers_path=EXAMPLE / "answers.json",
            output_dir=tmp_path,
            settings=settings,
        ),
        model=DeterministicWorkflowModel(),
        model_label="deterministic-workflow",
        medical_disclaimer="학생 과제용 실험 결과입니다.",
        write=lambda _: None,
    )
    session = ScreeningSession.model_validate_json(
        first.session_path.read_text(encoding="utf-8")
    )
    assert session.completed is False
    assert session.result.stop_reason.value == "awaiting_patient_choice"
    assert session.pending_option_id == option["option_id"]

    second = run_general_screening(
        options=GeneralRunOptions(
            patient_path=patient_path,
            trials_path=EXAMPLE / "trials.jsonl",
            answers_path=EXAMPLE / "answers.json",
            output_dir=tmp_path,
            resume_path=first.session_path,
            approve_patient_choice=True,
            settings=settings,
        ),
        model=DeterministicWorkflowModel(),
        model_label="deterministic-workflow",
        medical_disclaimer="학생 과제용 실험 결과입니다.",
        write=lambda _: None,
    )
    session = ScreeningSession.model_validate_json(
        second.session_path.read_text(encoding="utf-8")
    )
    assert session.completed is False
    assert session.result.stop_reason.value == "awaiting_clinician_authorization"
    assert option["option_id"] in session.patient_approved_option_ids

    third = run_general_screening(
        options=GeneralRunOptions(
            patient_path=patient_path,
            trials_path=EXAMPLE / "trials.jsonl",
            answers_path=EXAMPLE / "answers.json",
            output_dir=tmp_path,
            resume_path=second.session_path,
            authorize_clinician=True,
            settings=settings,
        ),
        model=DeterministicWorkflowModel(),
        model_label="deterministic-workflow",
        medical_disclaimer="학생 과제용 실험 결과입니다.",
        write=lambda _: None,
    )
    session = ScreeningSession.model_validate_json(
        third.session_path.read_text(encoding="utf-8")
    )
    assert session.completed is True
    assert session.result.stop_reason.value == "all_trials_resolved"
    assert option["option_id"] in session.clinician_authorized_option_ids
    assert session.action_count == 1


def test_unavailable_fact_is_not_repeated_until_retry_is_requested(
    tmp_path: Path,
) -> None:
    settings = EpisodeSettings(
        max_external_actions=3,
        max_selective_reviews=1,
        max_cycles=12,
    )
    first = run_general_screening(
        options=GeneralRunOptions(
            patient_path=EXAMPLE / "patient.json",
            trials_path=EXAMPLE / "trials.jsonl",
            output_dir=tmp_path,
            settings=settings,
        ),
        model=DeterministicWorkflowModel(),
        model_label="deterministic-workflow",
        medical_disclaimer="학생 과제용 실험 결과입니다.",
        read=lambda _: "unknown",
        write=lambda _: None,
    )
    session = ScreeningSession.model_validate_json(
        first.session_path.read_text(encoding="utf-8")
    )
    assert session.completed is False
    assert session.unavailable_fact_ids == ["recent-hba1c"]

    without_retry = run_general_screening(
        options=GeneralRunOptions(
            patient_path=EXAMPLE / "patient.json",
            trials_path=EXAMPLE / "trials.jsonl",
            output_dir=tmp_path,
            resume_path=first.session_path,
            settings=settings,
        ),
        model=DeterministicWorkflowModel(),
        model_label="deterministic-workflow",
        medical_disclaimer="학생 과제용 실험 결과입니다.",
        read=lambda _: pytest.fail("an unavailable fact must not be asked again"),
        write=lambda _: None,
    )
    session = ScreeningSession.model_validate_json(
        without_retry.session_path.read_text(encoding="utf-8")
    )
    assert session.action_count == 1

    answer = json.dumps(
        {
            "statement": "Official HbA1c result was 6.4 percent.",
            "concept": "hba1c",
            "value": 6.4,
            "unit": "%",
            "event_date": "2026-08-20",
            "recorded_date": "2026-08-20",
            "source_type": "official_verification",
            "source_location": "synthetic-official-result#hba1c",
            "verification_status": "verified",
        }
    )
    retried = run_general_screening(
        options=GeneralRunOptions(
            patient_path=EXAMPLE / "patient.json",
            trials_path=EXAMPLE / "trials.jsonl",
            output_dir=tmp_path,
            resume_path=without_retry.session_path,
            retry_unavailable=True,
            settings=settings,
        ),
        model=DeterministicWorkflowModel(),
        model_label="deterministic-workflow",
        medical_disclaimer="학생 과제용 실험 결과입니다.",
        read=lambda _: answer,
        write=lambda _: None,
    )
    session = ScreeningSession.model_validate_json(
        retried.session_path.read_text(encoding="utf-8")
    )
    assert session.completed is True
    assert session.unavailable_fact_ids == []
    assert session.action_count == 2


def test_plain_text_answer_keeps_user_report_provenance() -> None:
    patient = load_general_patient(EXAMPLE / "patient.json")

    evidence = evidence_from_user_input(
        raw="최근 검사에서 HbA1c가 6.4%라고 들었습니다.",
        action=NextAction.REQUEST_VERIFICATION,
        fact_id="recent-hba1c",
        patient_state=patient.patient_state,
        step=1,
    )

    assert evidence.source_type is EvidenceSourceType.PATIENT_REPORT
    assert evidence.verification_status is VerificationStatus.REPORTED
    assert evidence.event_date is None
    assert evidence.input_provenance is not None
    assert (
        evidence.input_provenance.capture_method
        is EvidenceCaptureMethod.INTERACTIVE_TEXT
    )
    assert evidence.input_provenance.requested_action is NextAction.REQUEST_VERIFICATION
    assert evidence.input_provenance.source_type_declared is False
    assert evidence.input_provenance.event_date_declared is False


def test_explicit_json_can_record_official_verified_result() -> None:
    patient = load_general_patient(EXAMPLE / "patient.json")
    raw = json.dumps(
        {
            "statement": "Official HbA1c result was 6.4 percent.",
            "concept": "hba1c",
            "value": 6.4,
            "unit": "%",
            "event_date": "2026-08-20",
            "recorded_date": "2026-08-20",
            "source_type": "official_verification",
            "source_location": "synthetic-official-result#hba1c",
            "verification_status": "verified",
        }
    )

    evidence = evidence_from_user_input(
        raw=raw,
        action=NextAction.REQUEST_VERIFICATION,
        fact_id="recent-hba1c",
        patient_state=patient.patient_state,
        step=1,
    )

    assert evidence.source_type is EvidenceSourceType.OFFICIAL_VERIFICATION
    assert evidence.verification_status is VerificationStatus.VERIFIED
    assert evidence.event_date.isoformat() == "2026-08-20"
    assert evidence.input_provenance is not None
    assert (
        evidence.input_provenance.capture_method
        is EvidenceCaptureMethod.INTERACTIVE_JSON
    )
    assert evidence.input_provenance.source_type_declared is True
    assert evidence.input_provenance.source_location_declared is True
    assert evidence.input_provenance.verification_status_declared is True
    assert evidence.input_provenance.event_date_declared is True
