from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def test_report_figures_are_reproducible_svg(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "scripts/render_report_figures.py",
            "--output-dir",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    expected = {
        "clarifytrial-question-policy-results.svg",
        "clarifytrial-patient-burden-results.svg",
        "clarifytrial-representative-case.svg",
    }
    assert {path.name for path in tmp_path.iterdir()} == expected

    for filename in expected:
        path = tmp_path / filename
        root = ET.parse(path).getroot()
        assert root.tag.endswith("svg")
        assert "ClarifyTrial" in path.read_text(encoding="utf-8")
