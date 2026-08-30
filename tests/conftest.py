from __future__ import annotations

from pathlib import Path

import pytest

from clarifytrial.cli import main


@pytest.fixture(scope="session")
def presentation_demo_result(tmp_path_factory: pytest.TempPathFactory) -> Path:
    output = tmp_path_factory.mktemp("presentation-demo-run")
    exit_code = main(
        [
            "run-screening",
            "--patient",
            "examples/general_screening/patient.json",
            "--trials",
            "examples/general_screening/trials.jsonl",
            "--answers",
            "examples/general_screening/presentation-answers.json",
            "--provider",
            "deterministic",
            "--output",
            str(output),
        ]
    )
    assert exit_code == 0
    result = output / "result.json"
    assert result.exists()
    return result
