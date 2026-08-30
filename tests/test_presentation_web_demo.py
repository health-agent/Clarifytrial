from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _run(input_path: Path, output_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "scripts/render_presentation_web_demo.py",
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_web_demo_is_self_contained_and_uses_saved_results(
    tmp_path: Path, presentation_demo_result: Path
) -> None:
    output = tmp_path / "presentation-demo.html"
    result = _run(presentation_demo_result, output)

    assert result.returncode == 0, result.stderr
    contents = output.read_text(encoding="utf-8")
    assert '<html lang="ko">' in contents
    assert 'data-stage="0"' in contents
    assert 'data-loop="4"' in contents
    assert "ClarifyTrial 에이전트 워크플로우" in contents
    assert "상태 → 결정 → 도구 → 갱신 → 반복 판단" in contents
    assert "NCT-SYNTH-A" in contents
    assert "NCT-SYNTH-B" in contents
    assert "HbA1c 6.5%" in contents
    assert "HbA1c 7.5%" in contents
    assert "당화혈색소" in contents
    assert "7% 미만" in contents
    assert "8% 미만" in contents
    assert "조건 불충족" in contents
    assert "확인 완료" in contents
    assert "recent-hba1c:existing-result" in contents
    assert "recent-hba1c:new-test" in contents
    assert "환자 제한 · 새 검사·추가 방문 없음" in contents
    assert "환자 제한에 맞지 않아 제외" in contents
    assert "미확정이 남으면 갱신된 상태로 돌아가" in contents
    assert "이번 사례: 미확정 0개 · 종료" in contents
    assert "반복 또는 종료 판단" in contents
    assert "1/5" in contents
    assert 'id="play"' in contents
    assert 'id="next"' in contents
    assert 'id="fullscreen"' in contents
    assert "https://" not in contents
    assert "http://" not in contents


def test_web_demo_rejects_a_missing_result(tmp_path: Path) -> None:
    output = tmp_path / "presentation-demo.html"
    result = _run(tmp_path / "missing.json", output)

    assert result.returncode == 2
    assert "실행 결과 파일이 없습니다" in result.stderr
    assert not output.exists()
