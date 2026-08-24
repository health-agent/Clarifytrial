from __future__ import annotations

from pathlib import Path

import pytest

from clarifytrial.llm.prompts import FilePromptLoader, repository_prompt_loader


def test_file_prompt_loader_reads_only_beneath_configured_root(tmp_path: Path) -> None:
    prompt = tmp_path / "prompts" / "matcher.md"
    prompt.parent.mkdir()
    prompt.write_text("role instructions", encoding="utf-8")
    loader = FilePromptLoader(tmp_path)

    assert loader("prompts/matcher.md") == "role instructions"


def test_file_prompt_loader_rejects_parent_path_escape(tmp_path: Path) -> None:
    loader = FilePromptLoader(tmp_path / "allowed")

    with pytest.raises(ValueError, match="outside"):
        loader("../secret.txt")


def test_file_prompt_loader_reports_missing_prompt_by_identifier(tmp_path: Path) -> None:
    loader = FilePromptLoader(tmp_path)

    with pytest.raises(FileNotFoundError, match="prompts/missing.md"):
        loader("prompts/missing.md")


def test_repository_prompt_loader_reads_a_core_role_prompt() -> None:
    assert "후보 유지" in repository_prompt_loader()("prompts/matcher_judge.md")
