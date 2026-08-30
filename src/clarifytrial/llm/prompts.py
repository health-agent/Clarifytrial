"""Load versioned role prompts without accepting paths outside the repository."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol


class PromptLoader(Protocol):
    """Callable boundary used by provider adapters."""

    def __call__(self, prompt_id: str) -> str:
        """Return the prompt text associated with one public prompt identifier."""


class FilePromptLoader:
    """Read UTF-8 prompts rooted at one explicit project directory."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()

    def __call__(self, prompt_id: str) -> str:
        relative = Path(prompt_id)
        if relative.is_absolute():
            raise ValueError("prompt_id must be a repository-relative path")

        source = (self._root / relative).resolve()
        if not source.is_relative_to(self._root):
            raise ValueError("prompt_id resolves outside the configured prompt root")
        if not source.is_file():
            raise FileNotFoundError(f"prompt file not found: {prompt_id}")
        return source.read_text(encoding="utf-8")


def repository_prompt_loader() -> FilePromptLoader:
    """Return the prompt loader in a source checkout or installed wheel."""

    module_path = Path(__file__).resolve()
    candidates = (
        module_path.parents[3],  # repository/src/clarifytrial/llm
        module_path.parents[2],  # installed-prefix/clarifytrial/llm
        Path(sys.prefix),  # data-files are installed under the active environment
    )
    for root in candidates:
        if (root / "prompts").is_dir():
            return FilePromptLoader(root)
    raise FileNotFoundError(
        "prompt directory was not installed; reinstall ClarifyTrial from the wheel"
    )
