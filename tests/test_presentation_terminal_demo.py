from __future__ import annotations

import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def _run(input_path: Path, output_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/render_presentation_terminal_demo.py",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_terminal_demo_uses_the_saved_interaction(
    tmp_path: Path, presentation_demo_result: Path
) -> None:
    output = tmp_path / "terminal-demo.svg"
    result = _run(presentation_demo_result, output)

    assert result.returncode == 0, result.stderr
    root = ET.parse(output).getroot()
    assert root.tag.endswith("svg")
    assert root.attrib["width"] == "1200"
    assert root.attrib["height"] == "675"
    contents = output.read_text(encoding="utf-8")
    assert "NCT-SYNTH-A  제외" in contents
    assert "NCT-SYNTH-B  현재 자료로 확인 완료" in contents
    assert "2026-08-20  /  공식검사  /  7.5%" in contents
    assert "7.0% 기준보다 0.5% 높음" in contents
    assert contents.count("공식검사 HbA1c · 2026-08-20") == 2
    assert "presentation-official-hba1c" not in contents
    assert "#FF6B6B" in contents


def test_terminal_demo_rejects_a_result_without_shared_evidence(
    tmp_path: Path, presentation_demo_result: Path
) -> None:
    document = json.loads(presentation_demo_result.read_text(encoding="utf-8"))
    document["screening"]["final_decisions"][1]["criterion_assessments"][0][
        "evidence_ids"
    ] = ["different-evidence"]
    invalid = tmp_path / "invalid.json"
    invalid.write_text(
        json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    output = tmp_path / "terminal-demo.svg"

    result = _run(invalid, output)

    assert result.returncode == 2
    assert "새 HbA1c 근거를 사용하지 않았습니다" in result.stderr
    assert not output.exists()


def test_terminal_demo_reports_a_missing_input_before_writing(tmp_path: Path) -> None:
    output = tmp_path / "terminal-demo.svg"
    result = _run(tmp_path / "missing.json", output)

    assert result.returncode == 2
    assert "실행 결과 파일이 없습니다" in result.stderr
    assert not output.exists()
