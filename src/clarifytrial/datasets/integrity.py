"""Portable integrity helpers for version-controlled text artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path


def portable_text_sha256(path: str | Path) -> str:
    """Hash text content without depending on the checkout newline style."""

    content = (
        Path(path).read_bytes().replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    )
    return hashlib.sha256(content).hexdigest()


__all__ = ["portable_text_sha256"]
