from pathlib import Path

from clarifytrial.cli import main
from clarifytrial.terminal_ui import run_natural_text_demo


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_text_demo_shows_questions_answers_and_decision_changes(tmp_path) -> None:
    lines: list[str] = []
    prompts: list[str] = []
    result = run_natural_text_demo(
        trial_set_path=(
            REPOSITORY_ROOT
            / "data"
            / "natural_evaluation_v1"
            / "preliminary_trial_set.json"
        ),
        generation_config_path=(
            REPOSITORY_ROOT
            / "configs"
            / "natural_evaluation_patient_generation_v2.json"
        ),
        patient_pairs_path=(
            REPOSITORY_ROOT
            / "data"
            / "natural_evaluation_v2"
            / "preliminary_patient_pairs.json"
        ),
        records_path=(
            REPOSITORY_ROOT
            / "data"
            / "natural_evaluation_v2"
            / "preliminary_natural_records.json"
        ),
        destination=tmp_path / "text-demo.json",
        patient_id="natural-breast_cancer-11",
        action_budget=3,
        input_state="fully-missing",
        auto_advance=False,
        read=lambda prompt: prompts.append(prompt) or "",
        write=lines.append,
    )

    text = "\n".join(lines)
    assert result["action_count"] == 3
    assert "핵심 값 다섯 개가 입력에서 모두 빠진 상태" in text
    assert "1번째 확인" in text
    assert "선택 이유:" in text
    assert "검증용 합성 답변:" in text
    assert "답변 뒤 바뀐 판정" in text
    assert "질문 선택 점검" in text
    assert "결과 개선에 필요하지 않았던 확인: 0개" in text
    assert "학생 과제용 실험 결과입니다." in text
    assert len(prompts) == 3
    assert all("Enter" in item for item in prompts)


def test_text_demo_cli_runs_without_external_model(tmp_path, capsys) -> None:
    exit_code = main(
        [
            "run-text-demo",
            "--patient-id",
            "natural-breast_cancer-11",
            "--action-budget",
            "3",
            "--auto",
            "--output",
            str(tmp_path / "cli-text-demo.json"),
        ]
    )

    output = capsys.readouterr().out
    assert exit_code == 0
    assert "ClarifyTrial 질문 과정" in output
    assert "상세 실행 기록:" in output
