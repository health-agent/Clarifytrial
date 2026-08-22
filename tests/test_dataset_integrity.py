from __future__ import annotations

from pathlib import Path

from clarifytrial.datasets.integrity import portable_text_sha256


def test_portable_text_hash_ignores_checkout_newlines(tmp_path: Path) -> None:
    windows_path = tmp_path / "windows.json"
    unix_path = tmp_path / "unix.json"
    windows_path.write_bytes(b'{\r\n  "value": 1\r\n}\r\n')
    unix_path.write_bytes(b'{\n  "value": 1\n}\n')

    assert portable_text_sha256(windows_path) == portable_text_sha256(unix_path)
