"""Render the report figures without external plotting dependencies."""

from __future__ import annotations

import argparse
from html import escape
from pathlib import Path


WIDTH = 1200
FONT = "Pretendard, Noto Sans KR, Malgun Gothic, sans-serif"
BACKGROUND = "#F5F7FB"
CARD = "#FFFFFF"
INK = "#172033"
MUTED = "#64748B"
LINE = "#DCE3EF"
BLUE = "#356AE6"
BLUE_LIGHT = "#EAF0FF"
GRAY_BAR = "#AAB5C5"


def text(
    x: int,
    y: int,
    value: str,
    *,
    size: int = 24,
    weight: int = 400,
    fill: str = INK,
    anchor: str = "start",
) -> str:
    return (
        f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">'
        f"{escape(value)}</text>"
    )


def multiline(
    x: int,
    y: int,
    lines: list[str],
    *,
    size: int = 24,
    weight: int = 400,
    fill: str = INK,
    line_height: int = 34,
    anchor: str = "start",
) -> str:
    spans = "".join(
        f'<tspan x="{x}" dy="{0 if index == 0 else line_height}">{escape(line)}</tspan>'
        for index, line in enumerate(lines)
    )
    return (
        f'<text x="{x}" y="{y}" font-family="{FONT}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}">{spans}</text>'
    )


def rect(
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    fill: str = CARD,
    stroke: str = "none",
    radius: int = 20,
) -> str:
    return (
        f'<rect x="{x}" y="{y}" width="{width}" height="{height}" rx="{radius}" '
        f'fill="{fill}" stroke="{stroke}"/>'
    )


def svg_document(height: int, body: list[str], title_value: str) -> str:
    return "\n".join(
        [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
            f'viewBox="0 0 {WIDTH} {height}" role="img" aria-labelledby="title desc">',
            f"<title id=\"title\">{escape(title_value)}</title>",
            f'<desc id="desc">ClarifyTrial {escape(title_value)}</desc>',
            f'<rect width="{WIDTH}" height="{height}" fill="{BACKGROUND}"/>',
            *body,
            "</svg>",
            "",
        ]
    )


def render_question_policy_results() -> str:
    height = 760
    body = [
        text(70, 82, "추가 정보를 최대 세 번 확인한 뒤 최종 상태가 맞은 시험", size=34, weight=700),
        text(
            70,
            124,
            "합성 환자 30명 · 시험 판단 150개 · 추가 확인은 최대 3번",
            size=20,
            fill=MUTED,
        ),
        rect(55, 165, 1090, 500),
    ]

    rows = [
        ("추가 정보를 확인하지 않고\n처음 환자 자료만 사용", 18.7, False),
        ("입력 파일에 적힌 순서대로\n최대 3개를 확인", 94.0, False),
        ("현재 가장 많은 미완료 시험에\n연결된 정보부터 확인", 95.3, False),
        ("남은 3번 안에 사용할 정보 조합을\n매번 다시 계산", 95.3, True),
    ]
    bar_x = 610
    max_width = 420
    for index, (label, value, current) in enumerate(rows):
        y = 205 + index * 100
        body.append(
            multiline(
                95,
                y + 15,
                label.split("\n"),
                size=18,
                weight=600 if current else 400,
                line_height=24,
            )
        )
        body.append(rect(bar_x, y, max_width, 48, fill="#EEF2F7", radius=12))
        body.append(
            rect(
                bar_x,
                y,
                round(max_width * value / 100),
                48,
                fill=BLUE if current else GRAY_BAR,
                radius=12,
            )
        )
        body.append(
            text(
                bar_x + round(max_width * value / 100) - 14,
                y + 32,
                f"{value:.1f}%",
                size=21,
                weight=700,
                fill="#FFFFFF",
                anchor="end",
            )
        )

    body.extend(
        [
            text(70, 716, "결과", size=19, weight=700, fill=BLUE),
            text(
                132,
                716,
                "28개 · 141개 · 143개 · 143개. 마지막 두 방법의 최종 결과는 같았다.",
                size=19,
            ),
        ]
    )
    return svg_document(height, body, "환자 정보가 빠졌을 때 먼저 확인할 정보 비교")


def render_patient_burden_results() -> str:
    height = 720
    body = [
        text(70, 82, "이동·비용 제한이 있을 때 확인 방법 선택 결과", size=36, weight=700),
        text(
            70,
            124,
            "합성 환자·자료 상황 360개 · 확인 방법 선택 1,800회",
            size=20,
            fill=MUTED,
        ),
        rect(55, 165, 530, 455),
        rect(615, 165, 530, 455),
        multiline(
            90,
            220,
            ["입력된 이동·비용 제한을 지키면서", "최종 판단까지 끝낸 시험 비율"],
            size=21,
            weight=650,
            line_height=34,
        ),
        multiline(
            650,
            220,
            ["새 검사·추가 방문이 어려운 환자에게", "그 방법을 제안한 횟수"],
            size=24,
            weight=650,
            line_height=34,
        ),
    ]

    left_rows = [
        ("모든 환자에게 같은 비용표", 81.0, False),
        ("이동·비용·시간 제한 반영", 88.5, True),
    ]
    for index, (label, value, current) in enumerate(left_rows):
        y = 330 + index * 110
        body.append(text(90, y, label, size=18, weight=600 if current else 400))
        body.append(rect(320, y - 28, 205, 42, fill="#EEF2F7", radius=10))
        body.append(
            rect(
                320,
                y - 28,
                round(205 * value / 100),
                42,
                fill=BLUE if current else GRAY_BAR,
                radius=10,
            )
        )
        body.append(text(525, y + 2, f"{value:.1f}%", size=22, weight=700, anchor="end"))

    right_rows = [
        ("모든 환자에게 같은 비용표", 65, False),
        ("이동·비용·시간 제한 반영", 0, True),
    ]
    for index, (label, value, current) in enumerate(right_rows):
        y = 330 + index * 110
        body.append(text(650, y, label, size=18, weight=600 if current else 400))
        body.append(rect(900, y - 28, 185, 42, fill="#EEF2F7", radius=10))
        if value:
            body.append(
                rect(
                    900,
                    y - 28,
                    round(185 * value / 70),
                    42,
                    fill=GRAY_BAR,
                    radius=10,
                )
            )
        else:
            body.append(f'<circle cx="912" cy="{y - 7}" r="8" fill="{BLUE}"/>')
        body.append(text(1085, y + 2, f"{value}회", size=22, weight=700, anchor="end"))

    body.extend(
        [
            text(70, 665, "제안이 0회였던 이유", size=19, weight=700, fill=BLUE),
            multiline(
                265,
                657,
                [
                    "기존 기록과 환자 답변을 먼저 사용했고,",
                    "허용된 방법이 없으면 판단을 보류했다.",
                ],
                size=17,
                line_height=24,
            ),
        ]
    )
    return svg_document(height, body, "환자 부담을 반영한 확인 방법 선택")


def render_representative_case() -> str:
    height = 770
    body = [
        text(70, 82, "대표 사례: 세 번 확인해 다섯 시험의 판단을 끝낸 순서", size=38, weight=700),
        text(
            70,
            124,
            "합성 유방암 환자 · 후보 시험 5개 · 부족한 정보 5개 · 확인 한도 3회",
            size=20,
            fill=MUTED,
        ),
        rect(55, 165, 1090, 475),
        f'<line x1="155" y1="355" x2="1045" y2="355" stroke="{LINE}" stroke-width="8" '
        'stroke-linecap="round"/>',
    ]

    steps = [
        (155, "시작", ["2 / 5", "판단 완료"]),
        (450, "1. 나이 확인", ["3 / 5", "판단 완료"]),
        (745, "2. 알부민 보정 혈중 칼슘 확인", ["4 / 5", "판단 완료"]),
        (1040, "3. 이전 전신치료 횟수 확인", ["5 / 5", "판단 완료"]),
    ]
    for index, (x, label, values) in enumerate(steps):
        active = index > 0
        body.append(
            f'<circle cx="{x}" cy="355" r="30" fill="{BLUE if active else CARD}" '
            f'stroke="{BLUE}" stroke-width="5"/>'
        )
        if active:
            body.append(text(x, 364, str(index), size=23, weight=700, fill="#FFFFFF", anchor="middle"))
        else:
            body.append(f'<circle cx="{x}" cy="355" r="8" fill="{BLUE}"/>')
        body.append(text(x, 295, label, size=20, weight=650, anchor="middle"))
        body.append(text(x, 430, values[0], size=34, weight=750, fill=BLUE, anchor="middle"))
        body.append(text(x, 465, values[1], size=18, fill=MUTED, anchor="middle"))

    body.extend(
        [
            rect(90, 525, 1020, 76, fill=BLUE_LIGHT, radius=16),
            text(120, 572, "처음 빠진 정보 목록의 앞 3개를 순서대로 확인하면 3 / 5에서 끝났다.", size=21, weight=650),
            text(70, 693, "선택 기준", size=19, weight=700, fill=BLUE),
            text(
                165,
                693,
                "한 정보를 확인했을 때 바뀌는 시험 수와, 남은 횟수 안에 끝낼 수 있는 시험 수를 함께 계산했다.",
                size=19,
            ),
            text(
                70,
                728,
                "이 사례는 설명용 합성 사례이며 실제 환자의 임상 결과를 뜻하지 않는다.",
                size=17,
                fill=MUTED,
            ),
        ]
    )
    return svg_document(height, body, "대표 합성 환자의 질문 순서")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("docs/internal/diagrams"),
        help="Directory in which to write the SVG files.",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    figures = {
        "clarifytrial-question-policy-results.svg": render_question_policy_results(),
        "clarifytrial-patient-burden-results.svg": render_patient_burden_results(),
        "clarifytrial-representative-case.svg": render_representative_case(),
    }
    for filename, contents in figures.items():
        output_path = args.output_dir / filename
        output_path.write_text(contents, encoding="utf-8")
        print(output_path)


if __name__ == "__main__":
    main()
