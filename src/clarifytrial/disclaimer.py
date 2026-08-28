"""One source for the concise notice attached to every user-facing result."""

from __future__ import annotations

from pathlib import Path


DEFAULT_MEDICAL_DISCLAIMER = (
    "이 결과는 의학적 조언이 아닌 참고용입니다. 임상시험 참가 가능성은 해당 "
    "시험의 최신 기준과 전체 자료를 바탕으로 다시 확인해야 합니다."
)


def read_medical_disclaimer() -> str:
    candidates = (
        Path.cwd() / "MEDICAL_DISCLAIMER.md",
        Path(__file__).resolve().parents[2] / "MEDICAL_DISCLAIMER.md",
    )
    for path in candidates:
        if not path.is_file():
            continue
        lines = [
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        if lines:
            return " ".join(lines)
    return DEFAULT_MEDICAL_DISCLAIMER


__all__ = ["DEFAULT_MEDICAL_DISCLAIMER", "read_medical_disclaimer"]
