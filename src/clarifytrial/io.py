"""Small atomic file writers shared by resumable workflow outputs."""

from __future__ import annotations

import os
import threading
from pathlib import Path


def atomic_write_text(
    path: str | Path,
    text: str,
    *,
    encoding: str = "utf-8",
) -> Path:
    """Replace one file only after its complete new contents are on disk."""

    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.{threading.get_ident()}.part"
    )
    try:
        temporary.write_text(text, encoding=encoding)
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return destination


__all__ = ["atomic_write_text"]
