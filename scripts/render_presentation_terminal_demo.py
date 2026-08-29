"""Render the verified presentation interaction as a terminal-style SVG."""

from __future__ import annotations

import argparse
import json
import math
import re
from dataclasses import dataclass
from html import escape
from pathlib import Path
from typing import Any, Mapping, Sequence


WIDTH = 1200
HEIGHT = 675
FONT = "Cascadia Mono, D2Coding, Consolas, Malgun Gothic, monospace"

BLACK = "#0D1117"
PANEL = "#111827"
NAVY = "#17324D"
BLUE = "#58A6FF"
INK = "#E6EDF3"
GRAY = "#9CA3AF"
LINE = "#2D3748"
RED = "#FF6B6B"


class DemoDataError(ValueError):
    """Raised when the saved interaction cannot support the demo figure."""


@dataclass(frozen=True)
class DemoTrial:
    trial_id: str
    threshold: float
    initial_status: str
    final_status: str
    evidence_id: str


@dataclass(frozen=True)
class DemoData:
    question: str
    event_date: str
    source_label: str
    value: float
    unit: str
    evidence_id: str
    first: DemoTrial
    second: DemoTrial


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise DemoDataError(f"{label} 항목이 객체가 아닙니다")
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, list):
        raise DemoDataError(f"{label} 항목이 목록이 아닙니다")
    return value


def _text_value(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DemoDataError(f"{label} 값이 없습니다")
    return value.strip()


def _number_value(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DemoDataError(f"{label} 값이 숫자가 아닙니다")
    number = float(value)
    if not math.isfinite(number):
        raise DemoDataError(f"{label} 값은 유한한 숫자여야 합니다")
    return number


def _by_id(items: Sequence[Any], key: str, label: str) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for item in items:
        record = _mapping(item, label)
        identifier = _text_value(record.get(key), f"{label}.{key}")
        result[identifier] = record
    return result


def _threshold(eligibility_text: str, trial_id: str) -> float:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%\s*미만", eligibility_text)
    if match is None:
        raise DemoDataError(f"{trial_id} 조건에서 HbA1c 기준값을 찾지 못했습니다")
    return float(match.group(1))


def _assessment_evidence_id(decision: Mapping[str, Any], trial_id: str) -> str:
    assessments = _sequence(
        decision.get("criterion_assessments"),
        f"{trial_id}.criterion_assessments",
    )
    if len(assessments) != 1:
        raise DemoDataError(f"{trial_id}의 발표 조건은 한 개여야 합니다")
    assessment = _mapping(assessments[0], f"{trial_id}.criterion_assessment")
    evidence_ids = _sequence(
        assessment.get("evidence_ids"), f"{trial_id}.evidence_ids"
    )
    if len(evidence_ids) != 1:
        raise DemoDataError(f"{trial_id}의 최종 근거가 한 개가 아닙니다")
    return _text_value(evidence_ids[0], f"{trial_id}.evidence_id")


def load_demo_data(path: Path) -> DemoData:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise DemoDataError(f"실행 결과 파일이 없습니다: {path}") from error
    except json.JSONDecodeError as error:
        raise DemoDataError(f"실행 결과 JSON을 읽을 수 없습니다: {error}") from error

    root = _mapping(document, "root")
    input_section = _mapping(root.get("input"), "input")
    screening = _mapping(root.get("screening"), "screening")

    candidate_hits: dict[str, Mapping[str, Any]] = {}
    for item in _sequence(input_section.get("candidate_hits"), "input.candidate_hits"):
        hit = _mapping(item, "candidate_hit")
        source = _mapping(hit.get("source"), "candidate_hit.source")
        trial_id = _text_value(source.get("trial_id"), "candidate_hit.source.trial_id")
        candidate_hits[trial_id] = source
    required_trials = ("NCT-SYNTH-A", "NCT-SYNTH-B")
    if any(trial_id not in candidate_hits for trial_id in required_trials):
        raise DemoDataError("발표용 시험 NCT-SYNTH-A와 NCT-SYNTH-B가 모두 필요합니다")

    histories = _sequence(screening.get("decision_history"), "decision_history")
    initial_cycle = next(
        (
            _mapping(item, "decision_history item")
            for item in histories
            if _mapping(item, "decision_history item").get("cycle") == 0
        ),
        None,
    )
    if initial_cycle is None:
        raise DemoDataError("질문 전 판단 기록(cycle 0)이 없습니다")
    initial_decisions = _by_id(
        _sequence(initial_cycle.get("decisions"), "cycle 0 decisions"),
        "trial_id",
        "initial decision",
    )

    final_decisions = _by_id(
        _sequence(screening.get("final_decisions"), "final_decisions"),
        "trial_id",
        "final decision",
    )

    action_history = _sequence(screening.get("action_history"), "action_history")
    if len(action_history) != 1:
        raise DemoDataError("발표 실행은 추가 확인 한 번을 포함해야 합니다")
    action = _mapping(action_history[0], "action_history[0]")
    agent_action = _mapping(action.get("agent_action"), "agent_action")
    question = _text_value(agent_action.get("message"), "agent_action.message")
    tool_result = _mapping(action.get("tool_result"), "tool_result")
    new_facts = _sequence(tool_result.get("new_facts"), "tool_result.new_facts")
    if len(new_facts) != 1:
        raise DemoDataError("발표 실행에서 새로 확인한 사실은 한 개여야 합니다")
    fact = _mapping(new_facts[0], "new_fact")
    evidence_id = _text_value(fact.get("evidence_id"), "new_fact.evidence_id")
    event_date = _text_value(fact.get("event_date"), "new_fact.event_date")
    value = _number_value(fact.get("value"), "new_fact.value")
    unit = _text_value(fact.get("unit"), "new_fact.unit")
    if fact.get("source_type") != "official_verification":
        raise DemoDataError("발표 입력의 출처는 official_verification이어야 합니다")
    if fact.get("concept") != "hba1c":
        raise DemoDataError("발표 입력은 HbA1c 결과여야 합니다")

    trials: list[DemoTrial] = []
    expected_final = {
        "NCT-SYNTH-A": ("remove", "ineligible", "제외"),
        "NCT-SYNTH-B": ("retain", "confirmed", "현재 자료로 확인 완료"),
    }
    for trial_id in required_trials:
        threshold = _threshold(
            _text_value(
                candidate_hits[trial_id].get("eligibility_text"),
                f"{trial_id}.eligibility_text",
            ),
            trial_id,
        )
        initial = initial_decisions.get(trial_id)
        final = final_decisions.get(trial_id)
        if initial is None or final is None:
            raise DemoDataError(f"{trial_id}의 질문 전후 판단이 모두 필요합니다")
        if initial.get("candidate_status") != "retain" or initial.get(
            "confirmation_status"
        ) != "not_confirmed":
            raise DemoDataError(f"{trial_id}의 시작 상태가 후보 유지·미확인이 아닙니다")
        pending = _sequence(initial.get("pending_information"), f"{trial_id}.pending")
        if not any(
            _mapping(item, f"{trial_id}.pending item").get("fact_id")
            == "recent-hba1c"
            for item in pending
        ):
            raise DemoDataError(f"{trial_id}가 최근 HbA1c 결과를 기다리지 않습니다")
        candidate_status, confirmation_status, final_label = expected_final[trial_id]
        if final.get("candidate_status") != candidate_status or final.get(
            "confirmation_status"
        ) != confirmation_status:
            raise DemoDataError(f"{trial_id}의 최종 상태가 발표 사례와 다릅니다")
        trial_evidence_id = _assessment_evidence_id(final, trial_id)
        if trial_evidence_id != evidence_id:
            raise DemoDataError(f"{trial_id}가 새 HbA1c 근거를 사용하지 않았습니다")
        trials.append(
            DemoTrial(
                trial_id=trial_id,
                threshold=threshold,
                initial_status="최근 공식 HbA1c 결과 대기",
                final_status=final_label,
                evidence_id=trial_evidence_id,
            )
        )

    if trials[0].evidence_id != trials[1].evidence_id:
        raise DemoDataError("두 시험이 같은 근거를 재사용하지 않았습니다")
    if value <= trials[0].threshold or value >= trials[1].threshold:
        raise DemoDataError("HbA1c 값이 두 시험을 서로 다른 결과로 나누지 않습니다")

    return DemoData(
        question=question,
        event_date=event_date,
        source_label="공식검사",
        value=value,
        unit=unit,
        evidence_id=evidence_id,
        first=trials[0],
        second=trials[1],
    )


def _text(
    x: float,
    y: float,
    value: str,
    *,
    size: int = 20,
    weight: int = 400,
    fill: str = INK,
    anchor: str = "start",
) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" '
        f'font-size="{size}" font-weight="{weight}" fill="{fill}" '
        f'text-anchor="{anchor}">{escape(value)}</text>'
    )


def _line(y: float) -> str:
    return f'<line x1="48" y1="{y:.1f}" x2="1152" y2="{y:.1f}" stroke="{LINE}"/>'


def render_terminal_demo(data: DemoData) -> str:
    first_difference = data.value - data.first.threshold
    value = f"{data.value:g}{data.unit}"
    evidence_label = f"{data.source_label} HbA1c · {data.event_date}"
    first_threshold = f"{data.first.threshold:.1f}{data.unit}"
    second_threshold = f"{data.second.threshold:.1f}{data.unit}"
    body = [
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{BLACK}"/>',
        f'<rect x="0" y="0" width="{WIDTH}" height="58" fill="{PANEL}"/>',
        f'<rect x="0" y="57" width="{WIDTH}" height="2" fill="{BLUE}"/>',
        _text(42, 38, "ClarifyTrial  |  질문 뒤 재판정", size=20, weight=700),
        _text(48, 94, "[시작 상태]", size=18, weight=700, fill=BLUE),
        _text(48, 130, data.first.trial_id, size=19, weight=700),
        _text(245, 130, f"후보 유지  ·  {data.first.initial_status}", size=19, fill=GRAY),
        _text(905, 130, f"조건  {first_threshold} 미만", size=18, fill=GRAY),
        _text(48, 164, data.second.trial_id, size=19, weight=700),
        _text(245, 164, f"후보 유지  ·  {data.second.initial_status}", size=19, fill=GRAY),
        _text(905, 164, f"조건  {second_threshold} 미만", size=18, fill=GRAY),
        _line(192),
        _text(48, 230, "질문 >", size=19, weight=700, fill=BLUE),
        _text(148, 230, data.question, size=19),
        _text(48, 270, "환자 입력 >", size=19, weight=700, fill=BLUE),
        _text(
            188,
            270,
            f"{data.event_date}  /  {data.source_label}  /  {value}",
            size=20,
            weight=700,
        ),
        _text(48, 310, "저장된 근거 >", size=18, weight=700, fill=BLUE),
        _text(208, 310, evidence_label, size=18, fill=GRAY),
        _line(338),
        _text(48, 378, "[재판정]", size=18, weight=700, fill=BLUE),
        _text(
            48,
            420,
            (
                f"{data.first.trial_id}  {data.first.final_status}  ·  {value}는 "
                f"{first_threshold} 기준보다 {first_difference:.1f}{data.unit} 높음"
            ),
            size=20,
            weight=700,
            fill=RED,
        ),
        _text(
            48,
            462,
            (
                f"{data.second.trial_id}  {data.second.final_status}  ·  {value}는 "
                f"{second_threshold} 미만"
            ),
            size=20,
            weight=700,
            fill=BLUE,
        ),
        _line(494),
        _text(48, 535, "[근거 사용]", size=18, weight=700, fill=BLUE),
        _text(48, 577, data.first.trial_id, size=19, weight=700),
        _text(48, 611, data.second.trial_id, size=19, weight=700),
        f'<line x1="185" y1="571" x2="238" y2="571" stroke="{GRAY}" stroke-width="2"/>',
        f'<line x1="185" y1="605" x2="258" y2="605" stroke="{GRAY}" stroke-width="2"/>',
        f'<line x1="238" y1="571" x2="238" y2="605" stroke="{GRAY}" stroke-width="2"/>',
        _text(270, 611, evidence_label, size=19, fill=GRAY),
        _text(710, 598, "같은 검사 결과를 두 시험의 조건에 각각 적용", size=19, fill=GRAY),
    ]
    return "\n".join(
        [
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" '
                f'height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" '
                'role="img" aria-labelledby="title desc">'
            ),
            "<title id=\"title\">ClarifyTrial 질문 뒤 재판정</title>",
            (
                "<desc id=\"desc\">최근 HbA1c 결과 하나를 입력한 뒤 두 임상시험의 "
                "판단이 서로 다르게 바뀌고 같은 근거가 재사용된 실제 합성 실행 결과</desc>"
            ),
            *body,
            "</svg>",
            "",
        ]
    )


def render(input_path: Path, output_path: Path) -> Path:
    data = load_demo_data(input_path)
    document = render_terminal_demo(data)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(document, encoding="utf-8")
    return output_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("runs/presentation-demo-interactive-20260830/result.json"),
        help="Saved interactive screening result JSON.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/internal/diagrams/clarifytrial-terminal-demo.svg"),
        help="Output SVG path.",
    )
    args = parser.parse_args()
    try:
        path = render(args.input, args.output)
    except DemoDataError as error:
        parser.exit(2, f"오류: {error}\n")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
