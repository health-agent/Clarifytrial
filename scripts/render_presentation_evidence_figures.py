"""Render the presentation evidence figures from the exported CSV tables.

The renderer deliberately has no plotting-library dependency.  It validates
all input tables before creating the output directory so that a partial
or stale presentation set cannot be mistaken for a complete one.
"""

from __future__ import annotations

import argparse
import csv
import math
from html import escape
from pathlib import Path
from typing import Iterable, Mapping, Sequence


WIDTH = 1200
HEIGHT = 675
FONT = "Pretendard, Noto Sans KR, Malgun Gothic, sans-serif"

BACKGROUND = "#F7F9FC"
WHITE = "#FFFFFF"
NAVY = "#17324D"
BLUE = "#2F6FED"
LIGHT_BLUE = "#7AA2F7"
PALE_BLUE = "#EAF0FF"
INK = "#182533"
MUTED = "#64748B"
GRAY = "#98A2B3"
LIGHT_GRAY = "#D8DEE8"
PALE_GRAY = "#EEF2F6"

REQUIRED_INPUTS = (
    "budget_policy_scores.csv",
    "budget_curve_auc.csv",
    "public_protocol_known_age_policy_metrics.csv",
    "public_protocol_known_age_paired_comparisons.csv",
    "public_protocol_common_facts_known_policy_metrics.csv",
    "public_protocol_common_facts_known_budget1.csv",
    "simple_vs_random_subgroups.csv",
    "burden_ablation_three_steps.csv",
    "route_choice_profile_results.csv",
    "shared_fact_coverage.csv",
)

OUTPUT_NAMES = (
    "clarifytrial-shared-information-coverage.svg",
    "clarifytrial-gray-zone-rescue.svg",
    "clarifytrial-public-budget-curves.svg",
    "clarifytrial-public-input-sensitivity.svg",
    "clarifytrial-structural-topology-budget1.svg",
    "clarifytrial-patient-limit-tradeoff.svg",
    "clarifytrial-route-choice.svg",
    "clarifytrial-compact-architecture.svg",
)


class FigureDataError(ValueError):
    """Raised when an input table cannot support the requested figure."""


def _text(
    x: float,
    y: float,
    value: object,
    *,
    size: int = 22,
    weight: int = 400,
    fill: str = INK,
    anchor: str = "start",
    opacity: float | None = None,
) -> str:
    opacity_attribute = "" if opacity is None else f' opacity="{opacity}"'
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" '
        f'font-size="{size}" font-weight="{weight}" fill="{fill}" '
        f'text-anchor="{anchor}"{opacity_attribute}>{escape(str(value))}</text>'
    )


def _multiline(
    x: float,
    y: float,
    lines: Sequence[str],
    *,
    size: int = 22,
    weight: int = 400,
    fill: str = INK,
    anchor: str = "start",
    line_height: int = 30,
) -> str:
    spans = "".join(
        f'<tspan x="{x:.1f}" dy="{0 if index == 0 else line_height}">'
        f"{escape(line)}</tspan>"
        for index, line in enumerate(lines)
    )
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{FONT}" '
        f'font-size="{size}" font-weight="{weight}" fill="{fill}" '
        f'text-anchor="{anchor}">{spans}</text>'
    )


def _rect(
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    fill: str = WHITE,
    stroke: str = "none",
    stroke_width: float = 1,
    radius: float = 16,
    dash: str | None = None,
) -> str:
    dash_attribute = "" if dash is None else f' stroke-dasharray="{dash}"'
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{width:.1f}" '
        f'height="{height:.1f}" rx="{radius:.1f}" fill="{fill}" '
        f'stroke="{stroke}" stroke-width="{stroke_width:.1f}"{dash_attribute}/>'
    )


def _line(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    *,
    stroke: str = LIGHT_GRAY,
    width: float = 1.5,
    dash: str | None = None,
    marker_end: bool = False,
) -> str:
    dash_attribute = "" if dash is None else f' stroke-dasharray="{dash}"'
    marker_attribute = ' marker-end="url(#arrow)"' if marker_end else ""
    return (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
        f'stroke="{stroke}" stroke-width="{width:.1f}"{dash_attribute}'
        f"{marker_attribute}/>"
    )


def _svg(title: str, description: str, body: Iterable[str]) -> str:
    return "\n".join(
        [
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" '
                f'height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}" '
                'role="img" aria-labelledby="title desc">'
            ),
            f'<title id="title">{escape(title)}</title>',
            f'<desc id="desc">{escape(description)}</desc>',
            "<defs>",
            (
                f'<marker id="arrow" viewBox="0 0 10 10" refX="8" refY="5" '
                'markerWidth="7" markerHeight="7" orient="auto-start-reverse">'
                f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{GRAY}"/></marker>'
            ),
            "</defs>",
            f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{BACKGROUND}"/>',
            *body,
            "</svg>",
            "",
        ]
    )


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames is None:
            raise FigureDataError(f"표의 첫 줄에 열 이름이 없습니다: {path.name}")
        rows = [dict(row) for row in reader]
    if not rows:
        raise FigureDataError(f"표에 데이터가 없습니다: {path.name}")
    return rows


def _number(row: Mapping[str, str], *names: str) -> float:
    for name in names:
        value = row.get(name)
        if value not in {None, ""}:
            try:
                number = float(value)
            except ValueError as error:
                raise FigureDataError(
                    f"{name!r} 값이 숫자가 아닙니다: {value!r}"
                ) from error
            if not math.isfinite(number):
                raise FigureDataError(f"{name!r} 값은 유한한 숫자여야 합니다")
            return number
    raise FigureDataError(f"필요한 숫자 열이 없습니다: {', '.join(names)}")


def _as_percent(value: float) -> float:
    return value * 100 if abs(value) <= 1.0000001 else value


def _format_percent(value: float, *, digits: int = 1) -> str:
    return f"{value:.{digits}f}%"


def _shared_coverage(
    rows: Sequence[Mapping[str, str]],
) -> tuple[float, int, int, int, int, int, int]:
    for row in rows:
        if any(
            key in row
            for key in (
                "share_of_criteria_with_a_cross_trial_fact",
                "coverage_rate",
                "shared_criterion_rate",
            )
        ):
            share = _as_percent(
                _number(
                    row,
                    "share_of_criteria_with_a_cross_trial_fact",
                    "coverage_rate",
                    "shared_criterion_rate",
                )
            )
            numerator = round(
                _number(
                    row,
                    "criteria_whose_fact_is_used_by_at_least_2_trials",
                    "shared_criterion_count",
                    "numerator",
                )
            )
            denominator = round(
                _number(
                    row,
                    "criterion_count",
                    "total_criterion_count",
                    "denominator",
                )
            )
            age = round(_number(row, "age_years_shared_criterion_count"))
            pregnancy = round(
                _number(row, "pregnancy_or_lactation_shared_criterion_count")
            )
            infection = round(
                _number(row, "active_serious_infection_shared_criterion_count")
            )
            other = round(_number(row, "other_shared_criterion_count"))
            if age + pregnancy + infection + other != numerator:
                raise FigureDataError("공통 정보 조건의 구성 합계가 전체와 맞지 않습니다")
            return share, numerator, denominator, age, pregnancy, infection, other

    raise FigureDataError("공통 정보 조건의 구성 열이 있는 한 행 형식이 필요합니다")


def render_shared_information_coverage(
    rows: Sequence[Mapping[str, str]],
) -> str:
    share, numerator, denominator, age, pregnancy, infection, other = (
        _shared_coverage(rows)
    )
    if denominator <= 0 or numerator < 0 or numerator > denominator:
        raise FigureDataError("공통 정보 조건 수와 전체 조건 수의 범위를 확인해 주세요")

    bar_x, bar_y, bar_width, bar_height = 90, 300, 1020, 94
    shared_width = bar_width * numerator / denominator
    body = [
        _text(70, 78, "환자 정보 하나를 확인하면 여러 시험이 함께 바뀔 수 있다", size=34, weight=700),
        _text(
            70,
            118,
            "공개 임상시험 50건에서 구조화한 참가 조건 202개를 살펴봤다",
            size=20,
            fill=MUTED,
        ),
        _text(70, 220, _format_percent(share), size=72, weight=700, fill=NAVY),
        _text(350, 205, "참가 조건이 다른 시험과", size=27, weight=600),
        _text(350, 240, "같은 환자 정보를 함께 썼다", size=27, weight=600),
        _rect(bar_x, bar_y, bar_width, bar_height, fill=PALE_GRAY, radius=18),
        _rect(bar_x, bar_y, shared_width, bar_height, fill=BLUE, radius=18),
        _text(
            bar_x + shared_width / 2,
            bar_y + 58,
            f"공통 정보가 쓰인 조건  {numerator}개",
            size=24,
            weight=700,
            fill=WHITE,
            anchor="middle",
        ),
        _text(
            bar_x + shared_width + (bar_width - shared_width) / 2,
            bar_y + 58,
            f"한 시험에서만 쓴 조건  {denominator - numerator}개",
            size=22,
            weight=600,
            fill=INK,
            anchor="middle",
        ),
        _rect(90, 470, 1020, 105, fill=WHITE, stroke=LIGHT_GRAY, radius=16),
        _multiline(
            125,
            505,
            [
                f"{numerator}개 중 {age + pregnancy + infection}개는 나이 {age}개, 임신·수유 {pregnancy}개, 활동성 감염 {infection}개였다.",
                "공통 정보는 분명했지만, 여러 검사값까지 폭넓게 공유됐다고 보기는 어렵다.",
            ],
            size=21,
            line_height=34,
        ),
        _text(
            70,
            630,
            "선택한 50개 시험과 202개 구조화 조건의 구성만 보여 주는 값이다.",
            size=17,
            fill=MUTED,
        ),
    ]
    return _svg(
        "여러 임상시험에서 함께 쓰이는 환자 정보",
        f"전체 조건 {denominator}개 중 {numerator}개, {share:.1f}%가 다른 시험과 환자 정보를 공유했다.",
        body,
    )


def _policy_metric_row(
    rows: Sequence[Mapping[str, str]],
    *,
    action_budget: int,
    policy_id: str,
) -> Mapping[str, str]:
    selected = [
        row
        for row in rows
        if int(float(row.get("action_budget", "-1"))) == action_budget
        and row.get("policy_id") == policy_id
    ]
    if len(selected) != 1:
        raise FigureDataError(
            f"확인 {action_budget}회 {policy_id} 결과가 하나여야 합니다: "
            f"{len(selected)}개"
        )
    return selected[0]


def render_gray_zone_rescue(
    rows: Sequence[Mapping[str, str]],
) -> str:
    """Show the observed pending-to-confirmed transitions after one check."""

    before = _policy_metric_row(rows, action_budget=0, policy_id="no_questions")
    after = _policy_metric_row(
        rows,
        action_budget=1,
        policy_id="clarifytrial_rule_v1",
    )
    patient_count = round(_number(after, "patient_count"))
    trial_count = round(_number(after, "trial_count"))
    pending_candidates = round(_number(before, "rescue_opportunity_count"))
    confirmed = round(_number(after, "confirmed_rescue_count"))
    action_count = round(_number(after, "mean_action_count") * patient_count)
    remaining = pending_candidates - confirmed
    if min(patient_count, trial_count, pending_candidates, confirmed, action_count) < 0:
        raise FigureDataError("질문 뒤 후보 확정 수의 범위를 확인해 주세요")
    if confirmed > pending_candidates or remaining < 0:
        raise FigureDataError("확정 후보 수가 시작 대기 후보보다 많습니다")

    body = [
        _text(
            70,
            72,
            (
                f"세 기본 문진 뒤 남은 {pending_candidates}건 중 {confirmed}건이 "
                "한 번의 확인으로 정리됐다"
            ),
            size=32,
            weight=700,
        ),
        _text(
            70,
            112,
            "나이·임신/수유·활동성 감염을 시작 자료에 넣은 뒤의 평가",
            size=19,
            fill=MUTED,
        ),
        _rect(70, 190, 340, 280, fill=WHITE, stroke=LIGHT_GRAY, radius=22),
        _text(240, 245, "확인 전", size=21, weight=700, fill=MUTED, anchor="middle"),
        _text(240, 335, f"{pending_candidates}건", size=68, weight=700, fill=NAVY, anchor="middle"),
        _multiline(
            240,
            385,
            ["후보로 남아 있었지만", "아직 참가 조건을 확정하지 못함"],
            size=20,
            weight=600,
            anchor="middle",
            line_height=30,
        ),
        _line(430, 330, 560, 330, stroke=BLUE, width=5, marker_end=True),
        _text(495, 275, "환자마다 최대 1회", size=18, weight=600, fill=NAVY, anchor="middle"),
        _text(495, 302, f"실제로 확인한 횟수 {action_count}회", size=18, fill=MUTED, anchor="middle"),
        _rect(580, 190, 550, 125, fill=PALE_BLUE, stroke=LIGHT_BLUE, radius=22),
        _text(625, 245, f"{confirmed}건", size=48, weight=700, fill=BLUE),
        _multiline(
            790,
            235,
            ["답을 받은 뒤", "참가 조건 확인 완료"],
            size=22,
            weight=700,
            line_height=31,
        ),
        _rect(580, 345, 550, 125, fill=WHITE, stroke=LIGHT_GRAY, radius=22),
        _text(625, 400, f"{remaining}건", size=48, weight=700, fill=GRAY),
        _multiline(
            790,
            390,
            ["한 번의 확인으로 부족해", "다음 확인을 기다림"],
            size=22,
            weight=700,
            line_height=31,
        ),
        _rect(70, 525, 1060, 70, fill=PALE_GRAY, radius=14),
        _text(
            600,
            568,
            f"합성 환자 {patient_count}명 × 각 5건 = 환자-시험 조합 {trial_count}개",
            size=21,
            weight=600,
            anchor="middle",
        ),
        _text(
            70,
            642,
            "열 개 질환에서 모두 한 건 이상 바뀌었으며, 실제 질문과 답변 뒤의 변화만 셌다.",
            size=16,
            fill=MUTED,
        ),
    ]
    return _svg(
        "질문 한 번 뒤 시험 상태 변화",
        f"확인 전 미확정 조합 {pending_candidates}개 중 {confirmed}개가 최대 한 번의 추가 확인 뒤 확인 완료로 바뀌었고 {remaining}개는 대기로 남았다.",
        body,
    )


POLICY_SPECS = (
    ("random_order_expectation", "가능한 정보 순서 전체 평균", GRAY, "7 7"),
    ("authored_order", "파일에 적힌 순서", NAVY, "12 6"),
    ("clarifytrial_rule_v1", "여러 시험에 필요한 정보 우선", BLUE, None),
)


def _public_curves(rows: Sequence[Mapping[str, str]]) -> list[dict[str, object]]:
    public = [
        row
        for row in rows
        if row.get("suite") == "public_patient_profiles"
        and row.get("evaluation_distribution") == "heldout"
    ]
    if not public:
        raise FigureDataError(
            "budget_curve_auc.csv에서 공개 환자 heldout 결과를 찾지 못했습니다"
        )
    by_policy = {row.get("policy_id", ""): row for row in public}
    exact = by_policy.get("clarifytrial_exact_coverage_v3")
    rule = by_policy.get("clarifytrial_rule_v1")
    if exact is None or rule is None:
        raise FigureDataError("budget_curve_auc.csv에 영향 우선 또는 전체 계산 결과가 없습니다")
    for budget in range(6):
        if abs(
            _number(exact, f"budget_{budget}_score")
            - _number(rule, f"budget_{budget}_score")
        ) > 1e-12:
            raise FigureDataError("영향 우선과 전체 계산 결과가 같다는 발표 문구를 확인해 주세요")
    result: list[dict[str, object]] = []
    for policy_id, label, color, dash in POLICY_SPECS:
        row = by_policy.get(policy_id)
        if row is None:
            raise FigureDataError(
                f"budget_curve_auc.csv에 발표 곡선 정책이 없습니다: {policy_id}"
            )
        values = [
            _as_percent(_number(row, f"budget_{budget}_score"))
            for budget in range(6)
        ]
        result.append(
            {
                "policy_id": policy_id,
                "label": label,
                "color": color,
                "dash": dash,
                "values": values,
                "auc": _number(
                    row,
                    "mean_trial_status_recovery_normalized_auc",
                    "normalized_auc",
                ),
            }
        )
    return result


def render_public_budget_curves(rows: Sequence[Mapping[str, str]]) -> str:
    curves = _public_curves(rows)
    values = [value for item in curves for value in item["values"]]  # type: ignore[index]
    y_min = max(0, math.floor((min(values) - 5) / 10) * 10)
    y_max = min(100, math.ceil((max(values) + 5) / 10) * 10)
    if y_max - y_min < 30:
        y_max = min(100, y_min + 30)

    x0, x1 = 105, 785
    y0, y1 = 540, 180

    def x_position(budget: int) -> float:
        return x0 + (x1 - x0) * budget / 5

    def y_position(value: float) -> float:
        return y0 - (value - y_min) / (y_max - y_min) * (y0 - y1)

    body = [
        _text(70, 72, "확인할 수 있는 정보가 적을수록 순서의 차이가 컸다", size=34, weight=700),
        _text(
            70,
            112,
            "10개 질환의 합성 환자 50명 · 최종 평가 30명 · 환자마다 공개 시험 5건",
            size=19,
            fill=MUTED,
        ),
    ]

    tick = int(math.ceil(y_min / 10) * 10)
    while tick <= y_max:
        y = y_position(tick)
        body.append(_line(x0, y, x1, y, stroke=LIGHT_GRAY, width=1))
        body.append(_text(x0 - 18, y + 7, f"{tick}%", size=17, fill=MUTED, anchor="end"))
        tick += 10
    body.extend(
        [
            _line(x0, y0, x1, y0, stroke=GRAY, width=1.5),
            _line(x0, y0, x0, y1, stroke=GRAY, width=1.5),
            _text(x0, 155, "시험 상태 일치율", size=18, fill=MUTED),
            _text((x0 + x1) / 2, 608, "확인한 환자 정보 수", size=19, fill=MUTED, anchor="middle"),
        ]
    )
    for budget in range(6):
        x = x_position(budget)
        body.append(_text(x, y0 + 34, str(budget), size=19, fill=MUTED, anchor="middle"))

    for item in curves:
        points = [
            (x_position(index), y_position(value))
            for index, value in enumerate(item["values"])  # type: ignore[arg-type]
        ]
        point_text = " ".join(f"{x:.1f},{y:.1f}" for x, y in points)
        dash_attribute = (
            "" if item["dash"] is None else f' stroke-dasharray="{item["dash"]}"'
        )
        body.append(
            f'<polyline points="{point_text}" fill="none" stroke="{item["color"]}" '
            f'stroke-width="4" stroke-linecap="round" stroke-linejoin="round"{dash_attribute}/>'
        )
        for x, y in points:
            body.append(
                f'<circle cx="{x:.1f}" cy="{y:.1f}" r="6" fill="{WHITE}" '
                f'stroke="{item["color"]}" stroke-width="3"/>'
            )

    legend_x, legend_y = 825, 185
    for index, item in enumerate(curves):
        y = legend_y + index * 105
        dash_attribute = (
            "" if item["dash"] is None else f' stroke-dasharray="{item["dash"]}"'
        )
        body.append(
            f'<line x1="{legend_x}" y1="{y}" x2="{legend_x + 42}" y2="{y}" '
            f'stroke="{item["color"]}" stroke-width="4"{dash_attribute}/>'
        )
        short_label = {
            "random_order_expectation": "가능한 정보 순서 전체 평균",
            "authored_order": "파일에 적힌 순서",
            "clarifytrial_rule_v1": "여러 시험에 필요한 정보 우선",
        }[item["policy_id"]]
        body.append(_text(legend_x + 58, y + 7, short_label, size=16, weight=600))
        first_value = item["values"][1]  # type: ignore[index]
        body.append(
            _text(
                legend_x + 58,
                y + 33,
                f"한 번 확인  {_format_percent(first_value)}",
                size=16,
                fill=MUTED,
            )
        )
        body.append(
            _text(
                legend_x + 58,
                y + 57,
                f"0~5회 곡선 면적  {float(item['auc']):.2f}",
                size=16,
                fill=MUTED,
            )
        )

    body.extend(
        [
            _rect(825, 520, 315, 70, fill=PALE_BLUE, radius=12),
            _multiline(
                850,
                548,
                [
                    "남은 횟수를 모두 계산한 방법도",
                    "영향 우선과 0~5회 모두 같았다.",
                ],
                size=16,
                weight=600,
                line_height=23,
            ),
        ]
    )

    body.append(
        _text(
            70,
            650,
            "같은 환자에게 붙인 시험 다섯 건을 따로 세지 않고, 합성 환자 30명을 통계 단위로 삼았다.",
            size=16,
            fill=MUTED,
        )
    )
    return _svg(
        "환자 정보를 확인하는 순서와 최종 상태 일치",
        "공개 조건 합성 환자에서 확인 기회 0회부터 5회까지 세 질문 순서의 최종 시험 상태 일치율을 비교한 선 그래프다.",
        body,
    )


def _budget_one_policy_value(
    rows: Sequence[Mapping[str, str]],
    policy_id: str,
) -> float:
    selected = [
        row
        for row in rows
        if int(float(row.get("action_budget", "-1"))) == 1
        and row.get("policy_id") == policy_id
    ]
    if len(selected) != 1:
        raise FigureDataError(
            f"확인 1회 {policy_id} 결과가 하나여야 합니다: {len(selected)}개"
        )
    return _as_percent(_number(selected[0], "mean_trial_status_recovery"))


def _budget_one_difference(
    rows: Sequence[Mapping[str, str]],
) -> tuple[float, float, float, int, int, int]:
    selected = [
        row
        for row in rows
        if int(float(row.get("action_budget", "-1"))) == 1
        and row.get("candidate_policy_id") == "clarifytrial_rule_v1"
        and row.get("baseline_policy_id") == "random_order_expectation"
        and row.get("metric") == "trial_status_recovery"
    ]
    if len(selected) != 1:
        raise FigureDataError(
            "확인 1회 영향 우선과 가능한 순서 평균의 비교가 하나여야 합니다"
        )
    row = selected[0]
    return (
        _as_percent(_number(row, "mean_difference")),
        _as_percent(_number(row, "bootstrap_95_lower")),
        _as_percent(_number(row, "bootstrap_95_upper")),
        int(float(row.get("wins", "0"))),
        int(float(row.get("ties", "0"))),
        int(float(row.get("losses", "0"))),
    )


def render_public_input_sensitivity(
    curves: Sequence[Mapping[str, str]],
    known_age_metrics: Sequence[Mapping[str, str]],
    known_age_comparisons: Sequence[Mapping[str, str]],
    common_facts_metrics: Sequence[Mapping[str, str]],
    common_facts_comparisons: Sequence[Mapping[str, str]],
) -> str:
    """Show how routine intake fields explain the large first public result."""

    public = [
        row
        for row in curves
        if row.get("suite") == "public_patient_profiles"
        and row.get("evaluation_distribution") == "heldout"
    ]
    by_policy = {row.get("policy_id", ""): row for row in public}
    if not {
        "random_order_expectation",
        "clarifytrial_rule_v1",
    }.issubset(by_policy):
        raise FigureDataError("처음 공개 평가의 확인 1회 결과가 없습니다")

    initial_random = _as_percent(
        _number(by_policy["random_order_expectation"], "budget_1_score")
    )
    initial_rule = _as_percent(
        _number(by_policy["clarifytrial_rule_v1"], "budget_1_score")
    )
    age_random = _budget_one_policy_value(
        known_age_metrics, "random_order_expectation"
    )
    age_rule = _budget_one_policy_value(known_age_metrics, "clarifytrial_rule_v1")
    age_diff, age_low, age_high, age_wins, age_ties, age_losses = (
        _budget_one_difference(known_age_comparisons)
    )
    common_random = _budget_one_policy_value(
        common_facts_metrics, "random_order_expectation"
    )
    common_rule = _budget_one_policy_value(
        common_facts_metrics, "clarifytrial_rule_v1"
    )
    common_diff, common_low, common_high, common_wins, common_ties, common_losses = (
        _budget_one_difference(common_facts_comparisons)
    )

    rows = (
        (
            "처음 평가",
            "나이를 포함한 기본 문진도 가림 · 첫 선택 29/30명 나이",
            initial_random,
            initial_rule,
            initial_rule - initial_random,
            None,
        ),
        (
            "나이를 시작 자료에 넣음",
            f"개선 {age_wins}명 · 동일 {age_ties}명 · 악화 {age_losses}명",
            age_random,
            age_rule,
            age_diff,
            (age_low, age_high),
        ),
        (
            "세 기본 항목을 시작 자료에 넣음",
            (
                "나이·임신/수유·활동성 감염 · "
                f"개선 {common_wins}명 · 동일 {common_ties}명 · 악화 {common_losses}명"
            ),
            common_random,
            common_rule,
            common_diff,
            (common_low, common_high),
        ),
    )

    body = [
        _text(70, 72, "36.5%포인트의 대부분은 기본 문진을 가린 설정에서 나왔다", size=32, weight=700),
        _text(
            70,
            112,
            "같은 합성 환자 30명 · 확인 기회 1회 · 두 질문 순서를 같은 정답으로 비교",
            size=18,
            fill=MUTED,
        ),
        _text(655, 170, "가능한 순서 전체 평균", size=17, weight=600, fill=MUTED, anchor="middle"),
        _text(850, 170, "정보 영향 우선", size=17, weight=600, fill=MUTED, anchor="middle"),
        _text(1055, 170, "차이", size=17, weight=600, fill=MUTED, anchor="middle"),
    ]
    for index, (label, note, baseline, candidate, difference, interval) in enumerate(rows):
        y = 245 + index * 150
        if index:
            body.append(_line(70, y - 76, 1130, y - 76, stroke=LIGHT_GRAY))
        body.extend(
            [
                _text(70, y - 8, label, size=23, weight=700, fill=NAVY),
                _text(70, y + 26, note, size=16, fill=MUTED),
                _text(655, y + 8, f"{baseline:.1f}%", size=31, weight=700, fill=GRAY, anchor="middle"),
                _line(720, y - 2, 785, y - 2, stroke=LIGHT_BLUE, width=3, marker_end=True),
                _text(850, y + 8, f"{candidate:.1f}%", size=31, weight=700, fill=BLUE, anchor="middle"),
                _text(1055, y + 8, f"+{difference:.2f}%p", size=29, weight=700, fill=NAVY, anchor="middle"),
            ]
        )
        if interval is not None:
            body.append(
                _text(
                    1055,
                    y + 38,
                    f"95% 범위 {interval[0]:+.1f}~{interval[1]:+.1f}%p",
                    size=15,
                    fill=MUTED,
                    anchor="middle",
                )
            )
    body.append(
        _text(
            70,
            650,
            "세 기본 항목을 넣은 뒤의 +0.44%p는 1명에서만 생겼다. 현재 공개 시험 묶음의 실사용 질문 절감 효과로 넓혀 말하지 않는다.",
            size=16,
            fill=MUTED,
        )
    )
    return _svg(
        "공개 시험 질문 순서 결과의 입력 민감도",
        "나이와 기본 문진 항목을 시작 자료에 넣을수록 질문 순서 차이가 36.5%포인트에서 0.44%포인트로 줄어든 결과다.",
        body,
    )


TOPOLOGY_LABELS = {
    "fully_shared": "다섯 시험이 한 정보를 함께 씀",
    "shared_hub": "하나가 여러 시험에 연결",
    "gated_hub": "공통 확인 뒤 개별 확인",
    "three_way": "시험 세 곳씩 정보가 겹침",
    "overlapping_pairs": "시험 두 곳씩 정보가 겹침",
    "chain": "연결이 차례로 이어짐",
    "low_overlap": "겹치는 정보가 적음",
    "cost_conflict": "영향과 확인 부담이 엇갈림",
    "fully_separated": "시험마다 필요한 정보가 완전히 다름",
}


def _topology_differences(rows: Sequence[Mapping[str, str]]) -> list[tuple[str, float]]:
    selected = [
        row
        for row in rows
        if row.get("suite") == "synthetic_graph_stress"
        and row.get("subgroup_type") == "graph_topology"
        and row.get("budget") in {"1", "1.0"}
        and row.get("candidate_policy_id") == "clarifytrial_rule_v1"
        and row.get("baseline_policy_id") == "random"
        and row.get("evaluation_distribution") == "similar_heldout"
    ]
    if not selected:
        raise FigureDataError(
            "simple_vs_random_subgroups.csv에서 확인 1회 구조별 결과를 찾지 못했습니다"
        )
    by_topology = {
        row.get("subgroup", ""): _as_percent(_number(row, "difference"))
        for row in selected
    }
    missing = [name for name in TOPOLOGY_LABELS if name not in by_topology]
    if missing:
        raise FigureDataError(
            "구조 실험 표에 다음 연결 유형이 없습니다: " + ", ".join(missing)
        )
    return [(name, by_topology[name]) for name in TOPOLOGY_LABELS]


def render_structural_topology(rows: Sequence[Mapping[str, str]]) -> str:
    differences = _topology_differences(rows)
    max_abs = max(1.0, max(abs(value) for _, value in differences))
    domain = math.ceil((max_abs + 1) / 5) * 5
    label_x, zero_x, plot_half = 355, 700, 365

    def x_position(value: float) -> float:
        return zero_x + value / domain * plot_half

    body = [
        _text(70, 72, "질문 순서의 차이는 정보가 연결된 모양에 따라 달라졌다", size=32, weight=700),
        _text(
            70,
            112,
            "구조 9종 × 각 200개 설정 · 현재 연결 수 우선과 단계별 무작위 선택의 차이 · 확인 한 번",
            size=19,
            fill=MUTED,
        ),
        _line(zero_x, 160, zero_x, 570, stroke=GRAY, width=1.5),
        _text(zero_x, 610, "0%p", size=17, fill=MUTED, anchor="middle"),
        _text(zero_x - plot_half, 610, f"−{domain}%p", size=17, fill=MUTED, anchor="middle"),
        _text(zero_x + plot_half, 610, f"+{domain}%p", size=17, fill=MUTED, anchor="middle"),
    ]
    for index, (topology, value) in enumerate(differences):
        y = 175 + index * 45
        is_endpoint = topology in {"fully_shared", "fully_separated"}
        if is_endpoint:
            body.append(_rect(58, y - 27, 1080, 48, fill=PALE_BLUE, radius=8))
        body.append(
            _text(
                label_x,
                y + 6,
                TOPOLOGY_LABELS[topology],
                size=19,
                weight=700 if is_endpoint else 500,
                anchor="end",
            )
        )
        x_value = x_position(value)
        bar_x = min(zero_x, x_value)
        bar_width = max(2, abs(x_value - zero_x))
        body.append(
            _rect(
                bar_x,
                y - 16,
                bar_width,
                27,
                fill=BLUE if topology == "fully_shared" else (GRAY if topology == "fully_separated" else LIGHT_BLUE),
                radius=4,
            )
        )
        label_anchor = "start" if value >= 0 else "end"
        label_offset = 12 if value >= 0 else -12
        body.append(
            _text(
                x_value + label_offset,
                y + 5,
                f"{value:+.1f}%p",
                size=18,
                weight=700,
                fill=NAVY,
                anchor=label_anchor,
            )
        )
        if topology == "fully_separated":
            body.append(
                _text(1125, y + 5, "겹침 없음", size=16, fill=MUTED, anchor="end")
            )
    body.append(
        _text(
            70,
            650,
            "공통 연결의 비율만으로 효과가 정해지지 않는다. 정보 하나가 판단이 끝나지 않은 후보 여러 건과 직접 연결되는 모양이 중요했다.",
            size=16,
            fill=MUTED,
        )
    )
    return _svg(
        "환자 정보와 임상시험의 연결 구조별 질문 순서 효과",
        "합성 연결 구조 아홉 종류에서 현재 남은 시험과 가장 많이 연결된 정보 우선 정책과 단계별 무작위 선택의 차이를 비교했다.",
        body,
    )


def _stage(rows: Sequence[Mapping[str, str]], prefix: str) -> Mapping[str, str]:
    row = next((item for item in rows if item.get("stage", "").startswith(prefix)), None)
    if row is None:
        raise FigureDataError(
            f"burden_ablation_three_steps.csv에서 {prefix}단계 결과를 찾지 못했습니다"
        )
    return row


def render_patient_limit_tradeoff(rows: Sequence[Mapping[str, str]]) -> str:
    before = _stage(rows, "1_")
    after = _stage(rows, "2_")
    new_test_before = round(_number(before, "new_test_total"))
    new_test_after = round(_number(after, "new_test_total"))
    visit_before = round(_number(before, "additional_visit_total"))
    visit_after = round(_number(after, "additional_visit_total"))
    pending_before = _number(before, "mean_pending_trial_count")
    pending_after = _number(after, "mean_pending_trial_count")
    resolved_before = round(_number(before, "fully_resolved_setting_count"))
    resolved_after = round(_number(after, "fully_resolved_setting_count"))
    setting_count = round(_number(before, "setting_pair_count"))
    body = [
        _text(70, 72, "환자가 이용하기 어려운 확인 방법은 질문 후보에서 먼저 뺐다", size=34, weight=700),
        _text(
            70,
            112,
            "합성 환자 20명 · 같은 질문 정책에서 이용 제한 적용 여부만 변경",
            size=19,
            fill=MUTED,
        ),
        _text(85, 185, "제안한 확인 방법", size=24, weight=700, fill=NAVY),
        _text(650, 185, "제한을 지킨 뒤 남은 대기", size=24, weight=700, fill=NAVY),
        _rect(70, 215, 490, 305, fill=WHITE, stroke=LIGHT_GRAY, radius=18),
        _rect(630, 215, 500, 330, fill=WHITE, stroke=LIGHT_GRAY, radius=18),
    ]

    for index, (label, first, second) in enumerate(
        (
            ("새 검사", new_test_before, new_test_after),
            ("추가 방문", visit_before, visit_after),
        )
    ):
        y = 295 + index * 125
        body.append(_text(105, y, label, size=23, weight=600))
        body.append(_text(285, y, str(first), size=44, weight=700, fill=GRAY, anchor="middle"))
        body.append(_line(330, y - 13, 390, y - 13, stroke=GRAY, width=2.5, marker_end=True))
        body.append(_text(465, y, str(second), size=44, weight=700, fill=BLUE, anchor="middle"))
    body.extend(
        [
            _text(285, 485, "제한 적용 전", size=16, fill=MUTED, anchor="middle"),
            _text(465, 485, "제한 적용 뒤", size=16, fill=MUTED, anchor="middle"),
        ]
    )

    body.extend(
        [
            _text(665, 275, "설정 하나에 남은 확인 대기 시험", size=20, weight=600),
            _text(835, 335, f"{pending_before:.2f}개", size=38, weight=700, fill=GRAY, anchor="middle"),
            _line(885, 322, 935, 322, stroke=GRAY, width=2.5, marker_end=True),
            _text(1010, 335, f"{pending_after:.2f}개", size=38, weight=700, fill=BLUE, anchor="middle"),
            _line(665, 375, 1095, 375, stroke=LIGHT_GRAY, width=1),
            _text(665, 425, "시험 다섯 건을 모두 정리한 설정", size=20, weight=600),
            _text(835, 490, f"{resolved_before}/{setting_count}", size=38, weight=700, fill=GRAY, anchor="middle"),
            _line(885, 477, 935, 477, stroke=GRAY, width=2.5, marker_end=True),
            _text(1010, 490, f"{resolved_after}/{setting_count}", size=38, weight=700, fill=BLUE, anchor="middle"),
        ]
    )

    body.extend(
        [
            _multiline(
                70,
                595,
                [
                    "새 검사와 방문을 피한 만큼 확인 대기 시험이 늘어났다.",
                    "끝까지 정리된 설정도 80개 가운데 42개에서 32개로 줄었다.",
                ],
                size=17,
                fill=MUTED,
                line_height=25,
            ),
        ]
    )
    return _svg(
        "환자가 이용할 수 있는 확인 방법을 반영한 결과",
        "새 검사와 추가 방문 제안이 사라진 대신 확인 대기 시험이 늘고, 다섯 시험을 모두 정리한 설정은 줄었다.",
        body,
    )


def render_route_choice(rows: Sequence[Mapping[str, str]]) -> str:
    by_profile = {item.get("patient_profile_id", ""): item for item in rows}
    required = {"low_extra_burden", "mobility_cost_constrained", "time_urgent"}
    if not required.issubset(by_profile):
        raise FigureDataError(
            "route_choice_profile_results.csv에 세 환자 상황 결과가 모두 필요합니다"
        )
    low = by_profile["low_extra_burden"]
    constrained = by_profile["mobility_cost_constrained"]
    urgent = by_profile["time_urgent"]
    existing_low = round(_number(low, "existing_official_result_count"))
    existing_constrained = round(
        _number(constrained, "existing_official_result_count")
    )
    urgent_tests = round(_number(urgent, "new_noninvasive_test_count"))
    same = round(_number(low, "same_final_judgment_masked_case_count"))

    body = [
        _text(70, 72, "같은 정보를 얻는 방법은 환자 상황에 따라 달라졌다", size=34, weight=700),
        _text(
            70,
            112,
            "합성 환자 20명 · 가린 사례 40개 · 두 방법 모두 같은 답을 제공",
            size=19,
            fill=MUTED,
        ),
        _rect(70, 155, 500, 128, fill=WHITE, stroke=LIGHT_GRAY, radius=18),
        _rect(630, 155, 500, 128, fill=WHITE, stroke=LIGHT_GRAY, radius=18),
        _text(105, 195, "외부 기관의 기존 공식 결과 받기", size=24, weight=700, fill=NAVY),
        _text(105, 232, "합성 대기 72시간 · 방문 없음 · 낮은 비용", size=19, fill=MUTED),
        _text(665, 195, "같은 검사를 새로 받기", size=24, weight=700, fill=NAVY),
        _text(665, 232, "합성 대기 8시간 · 방문 필요 · 중간 비용", size=19, fill=MUTED),
        _line(320, 300, 320, 355, stroke=GRAY, width=2, marker_end=True),
        _line(880, 300, 880, 355, stroke=GRAY, width=2, marker_end=True),
        _rect(70, 370, 500, 155, fill=PALE_GRAY, radius=18),
        _rect(630, 370, 500, 155, fill=PALE_BLUE, radius=18),
        _text(105, 412, "추가 부담을 줄이거나 이동·비용 제한이 있음", size=21, weight=700),
        _text(105, 463, f"기존 결과 받기  {existing_low}회 · {existing_constrained}회", size=32, weight=700, fill=NAVY),
        _text(665, 412, "확인 속도가 가장 중요함", size=21, weight=700),
        _text(665, 463, f"새 검사  {urgent_tests}회", size=32, weight=700, fill=BLUE),
        _rect(70, 555, 1060, 60, fill=NAVY, radius=14),
        _text(
            600,
            593,
            f"정보 순서와 시험별 최종 상태는 {same}/40개 사례에서 같았고, 확인 방법만 달라졌다.",
            size=21,
            weight=700,
            fill=WHITE,
            anchor="middle",
        ),
        _text(
            70,
            650,
            "시간과 비용은 경로 선택 동작을 확인하려고 정한 합성 값이며 실제 병원 대기시간을 뜻하지 않는다.",
            size=16,
            fill=MUTED,
        ),
    ]
    return _svg(
        "환자 상황에 따른 확인 방법 선택",
        "같은 답을 얻는 기존 공식 결과 회수와 새 검사를 놓고 환자 상황에 따라 선택 경로만 달라지는 통제 실험이다.",
        body,
    )


def render_compact_architecture() -> str:
    body = [
        _text(70, 68, "전체 순서는 코드가 관리한다", size=34, weight=700),
        _text(
            70,
            106,
            "자유 문장이나 뜻이 모호한 조건이 있을 때 여섯 역할 가운데 필요한 역할만 부른다",
            size=20,
            fill=MUTED,
        ),
    ]

    top_nodes = (
        (70, "환자와 시험 입력"),
        (345, "후보 검색과 조건 정리"),
        (620, "조건별 판단"),
        (895, "두 가지 상태 집계"),
    )
    bottom_nodes = (
        (895, "확인할 정보와 방법 선택"),
        (620, "답변 반영"),
        (345, "관련 조건만 다시 판단"),
        (70, "결과와 근거 저장"),
    )
    node_width, node_height = 235, 86

    role_nodes = (
        (70, "환자 기록 정리", 187),
        (330, "시험 관련성 확인", 430),
        (590, "시험 조건 정리", 505),
        (850, "조건 판단", 737),
    )
    for x, label, target_x in role_nodes:
        body.append(
            _rect(
                x,
                135,
                220,
                42,
                fill=PALE_GRAY,
                stroke=GRAY,
                radius=10,
                dash="5 4",
            )
        )
        body.append(
            _text(x + 110, 162, label, size=16, weight=600, fill=NAVY, anchor="middle")
        )
        body.append(_line(x + 110, 177, target_x, 225, stroke=GRAY, width=1.3, dash="4 4"))

    for index, (x, label) in enumerate(top_nodes):
        body.append(_rect(x, 215, node_width, node_height, fill=NAVY if index == 3 else WHITE, stroke=NAVY, stroke_width=2, radius=14))
        body.append(
            _text(
                x + node_width / 2,
                266,
                label,
                size=20,
                weight=700,
                fill=WHITE if index == 3 else INK,
                anchor="middle",
            )
        )
        if index < len(top_nodes) - 1:
            body.append(_line(x + node_width + 10, 258, x + 265, 258, stroke=GRAY, width=2.2, marker_end=True))

    body.append(_line(1012, 311, 1012, 400, stroke=GRAY, width=2.2, marker_end=True))

    for index, (x, label) in enumerate(bottom_nodes):
        body.append(_rect(x, 415, node_width, node_height, fill=BLUE if index == 0 else WHITE, stroke=BLUE if index == 0 else NAVY, stroke_width=2, radius=14))
        body.append(
            _text(
                x + node_width / 2,
                466,
                label,
                size=19,
                weight=700,
                fill=WHITE if index == 0 else INK,
                anchor="middle",
            )
        )
        if index < len(bottom_nodes) - 1:
            body.append(_line(x - 10, 458, x - 40, 458, stroke=GRAY, width=2.2, marker_end=True))

    bottom_roles = (
        (362, "근거 충돌 검토", 462),
        (912, "질문 문장 작성", 1012),
    )
    for x, label, target_x in bottom_roles:
        body.append(
            _rect(
                x,
                540,
                200,
                42,
                fill=PALE_GRAY,
                stroke=GRAY,
                radius=10,
                dash="5 4",
            )
        )
        body.append(
            _text(x + 100, 567, label, size=16, weight=600, fill=NAVY, anchor="middle")
        )
        body.append(_line(target_x, 505, x + 100, 540, stroke=GRAY, width=1.3, dash="4 4"))

    body.append(_line(187, 411, 187, 316, stroke=GRAY, width=1.5, dash="7 6", marker_end=True))
    body.append(_text(216, 364, "정보가 더 필요하면 반복", size=17, fill=MUTED))
    body.extend(
        [
            _rect(70, 615, 22, 22, fill=WHITE, stroke=NAVY, stroke_width=2, radius=5),
            _text(105, 632, "항상 실행하는 코드", size=16, fill=MUTED),
            _rect(300, 615, 22, 22, fill=PALE_GRAY, stroke=GRAY, radius=5, dash="4 3"),
            _text(335, 632, "문장을 읽을 때 부르는 모델 역할", size=16, fill=MUTED),
        ]
    )
    return _svg(
        "ClarifyTrial 전체 실행 구조",
        "코드가 검색, 조건 판단, 상태 집계, 추가 확인, 답변 반영, 재판정을 관리하고 자유 문장이 필요한 여섯 역할만 선택적으로 부른다.",
        body,
    )


def _validate_inputs(input_dir: Path) -> dict[str, list[dict[str, str]]]:
    missing = [name for name in REQUIRED_INPUTS if not (input_dir / name).is_file()]
    if missing:
        raise FileNotFoundError(
            "발표 그림을 만들 수 없습니다. 다음 결과 표가 없습니다: "
            + ", ".join(missing)
        )
    return {name: _read_csv(input_dir / name) for name in REQUIRED_INPUTS}


def render_all(input_dir: Path, output_dir: Path) -> list[Path]:
    tables = _validate_inputs(input_dir)
    # Render every figure in memory before touching the output directory.  A
    # malformed table therefore cannot leave a partly refreshed figure set.
    documents = {
        OUTPUT_NAMES[0]: render_shared_information_coverage(
            tables["shared_fact_coverage.csv"]
        ),
        OUTPUT_NAMES[1]: render_gray_zone_rescue(
            tables["public_protocol_common_facts_known_policy_metrics.csv"]
        ),
        OUTPUT_NAMES[2]: render_public_budget_curves(
            tables["budget_curve_auc.csv"]
        ),
        OUTPUT_NAMES[3]: render_public_input_sensitivity(
            tables["budget_curve_auc.csv"],
            tables["public_protocol_known_age_policy_metrics.csv"],
            tables["public_protocol_known_age_paired_comparisons.csv"],
            tables["public_protocol_common_facts_known_policy_metrics.csv"],
            tables["public_protocol_common_facts_known_budget1.csv"],
        ),
        OUTPUT_NAMES[4]: render_structural_topology(
            tables["simple_vs_random_subgroups.csv"]
        ),
        OUTPUT_NAMES[5]: render_patient_limit_tradeoff(
            tables["burden_ablation_three_steps.csv"]
        ),
        OUTPUT_NAMES[6]: render_route_choice(
            tables["route_choice_profile_results.csv"]
        ),
        OUTPUT_NAMES[7]: render_compact_architecture(),
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, document in documents.items():
        path = output_dir / name
        path.write_text(document, encoding="utf-8")
        paths.append(path)
    return paths


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("docs/internal/results/presentation-evidence-v2"),
        help="Directory containing the presentation evidence CSV files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/internal/diagrams"),
        help="Directory in which to write the SVG figures.",
    )
    args = parser.parse_args()
    try:
        paths = render_all(args.input_dir, args.output_dir)
    except (FileNotFoundError, FigureDataError) as error:
        parser.exit(2, f"오류: {error}\n")
    for path in paths:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
