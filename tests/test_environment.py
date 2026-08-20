from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from clarifytrial.contracts import (
    AgentAction,
    EvidenceFact,
    EvidenceSourceType,
    NextAction,
    PatientState,
    VerificationStatus,
)
from clarifytrial.environment import (
    EnvironmentStatus,
    HiddenFactAnswer,
    HiddenPatientEnvironment,
    PublicFactRequest,
    PublicQuestionCatalog,
    SyntheticInformationTools,
)


def evidence(
    evidence_id: str,
    statement: str,
    source_type: EvidenceSourceType,
    verification_status: VerificationStatus,
) -> EvidenceFact:
    return EvidenceFact(
        evidence_id=evidence_id,
        statement=statement,
        source_type=source_type,
        source_location=f"synthetic-case/{evidence_id}",
        event_date=date(2026, 8, 1),
        recorded_date=date(2026, 8, 2),
        verification_status=verification_status,
    )


def build_tools() -> tuple[SyntheticInformationTools, HiddenPatientEnvironment]:
    catalog = PublicQuestionCatalog(
        [
            PublicFactRequest(
                fact_id="recent-lab",
                description="최근 14일 이내 혈액검사 결과",
                available_actions=(NextAction.LOOKUP_RECORD,),
            ),
            PublicFactRequest(
                fact_id="current-medication",
                description="현재 복용 중인 약",
                available_actions=(NextAction.ASK_PATIENT,),
            ),
            PublicFactRequest(
                fact_id="central-review",
                description="중앙검사실의 공식 확인 결과",
                available_actions=(NextAction.REQUEST_VERIFICATION,),
            ),
        ]
    )
    environment = HiddenPatientEnvironment(
        [
            HiddenFactAnswer(
                fact_id="recent-lab",
                access_path=NextAction.LOOKUP_RECORD,
                evidence=evidence(
                    "e-record",
                    "2026-08-01 혈액검사에서 정해진 범위 안의 수치가 기록됨",
                    EvidenceSourceType.MEDICAL_RECORD,
                    VerificationStatus.VERIFIED,
                ),
            ),
            HiddenFactAnswer(
                fact_id="current-medication",
                access_path=NextAction.ASK_PATIENT,
                evidence=evidence(
                    "e-patient",
                    "합성 환자는 현재 복용 중인 약이 있다고 답함",
                    EvidenceSourceType.PATIENT_REPORT,
                    VerificationStatus.REPORTED,
                ),
            ),
            HiddenFactAnswer(
                fact_id="central-review",
                access_path=NextAction.REQUEST_VERIFICATION,
                evidence=evidence(
                    "e-official",
                    "중앙검사실이 합성 검사 결과를 공식 확인함",
                    EvidenceSourceType.OFFICIAL_VERIFICATION,
                    VerificationStatus.VERIFIED,
                ),
            ),
        ]
    )
    return SyntheticInformationTools(catalog, environment), environment


def action(path: NextAction, fact_id: str) -> AgentAction:
    return AgentAction(
        action=path,
        target_fact_id=fact_id,
        related_criterion_ids=["criterion-1"],
        reason="현재 판단에 필요한 사실을 확인한다.",
        message="합성 평가 환경에 정해진 사실을 요청한다.",
    )


def empty_state() -> PatientState:
    return PatientState(patient_id="synthetic-patient-1", as_of=date(2026, 8, 20), facts=[])


def test_public_catalog_contains_no_hidden_answer_or_label() -> None:
    tools, _ = build_tools()
    public_rows = tools.public_requests()
    serialized = [row.model_dump(mode="json") for row in public_rows]

    assert serialized[0] == {
        "fact_id": "recent-lab",
        "description": "최근 14일 이내 혈액검사 결과",
        "available_actions": ["LOOKUP_RECORD"],
    }
    text = repr(serialized)
    assert "정해진 범위" not in text
    assert "e-record" not in text


@pytest.mark.parametrize(
    ("path", "fact_id", "source_type"),
    [
        (NextAction.LOOKUP_RECORD, "recent-lab", EvidenceSourceType.MEDICAL_RECORD),
        (NextAction.ASK_PATIENT, "current-medication", EvidenceSourceType.PATIENT_REPORT),
        (
            NextAction.REQUEST_VERIFICATION,
            "central-review",
            EvidenceSourceType.OFFICIAL_VERIFICATION,
        ),
    ],
)
def test_each_path_returns_only_its_authored_source(
    path: NextAction,
    fact_id: str,
    source_type: EvidenceSourceType,
) -> None:
    tools, _ = build_tools()

    result = tools.execute(action(path, fact_id), empty_state())

    assert result.status is EnvironmentStatus.REVEALED
    assert len(result.new_facts) == 1
    assert result.new_facts[0].source_type is source_type
    assert result.new_facts[0].source_location
    assert result.new_facts[0].event_date is not None
    assert result.new_facts[0].recorded_date is not None
    assert result.new_facts[0].verification_status is not None
    assert result.patient_state.facts == result.new_facts


def test_wrong_path_does_not_reveal_a_hidden_fact() -> None:
    tools, environment = build_tools()

    result = tools.execute(
        action(NextAction.ASK_PATIENT, "recent-lab"),
        empty_state(),
    )

    assert result.status is EnvironmentStatus.NOT_AVAILABLE
    assert result.new_facts == []
    assert result.patient_state.facts == []
    assert environment.revealed_fact_ids == frozenset()


def test_none_and_defer_never_return_a_fact() -> None:
    _, environment = build_tools()

    none_result = environment.execute(NextAction.NONE, None)
    defer_result = environment.execute(NextAction.DEFER, "recent-lab")

    assert none_result.status is EnvironmentStatus.NO_FACT
    assert defer_result.status is EnvironmentStatus.NO_FACT
    assert none_result.new_facts == []
    assert defer_result.new_facts == []
    assert environment.revealed_fact_ids == frozenset()


def test_duplicate_request_is_an_explicit_no_op() -> None:
    tools, environment = build_tools()
    first = tools.execute(
        action(NextAction.LOOKUP_RECORD, "recent-lab"),
        empty_state(),
    )

    repeated = tools.execute(
        action(NextAction.LOOKUP_RECORD, "recent-lab"),
        first.patient_state,
    )

    assert first.status is EnvironmentStatus.REVEALED
    assert repeated.status is EnvironmentStatus.ALREADY_REVEALED
    assert repeated.new_facts == []
    assert len(repeated.patient_state.facts) == 1
    assert environment.revealed_fact_ids == frozenset({"recent-lab"})


def test_hidden_answer_rejects_source_from_another_path() -> None:
    with pytest.raises(ValidationError, match="medical_record"):
        HiddenFactAnswer(
            fact_id="recent-lab",
            access_path=NextAction.LOOKUP_RECORD,
            evidence=evidence(
                "wrong-source",
                "환자 답변을 기록 조회 결과로 잘못 연결한 예",
                EvidenceSourceType.PATIENT_REPORT,
                VerificationStatus.REPORTED,
            ),
        )


def test_hidden_answer_requires_event_and_recorded_dates() -> None:
    undated = evidence(
        "undated-record",
        "날짜가 빠진 합성 기록",
        EvidenceSourceType.MEDICAL_RECORD,
        VerificationStatus.VERIFIED,
    ).model_copy(update={"event_date": None})

    with pytest.raises(ValidationError, match="event_date and recorded_date"):
        HiddenFactAnswer(
            fact_id="recent-lab",
            access_path=NextAction.LOOKUP_RECORD,
            evidence=undated,
        )
