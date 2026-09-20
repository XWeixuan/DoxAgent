"""Read deployment tokens without leaking them into diagnostics."""

from __future__ import annotations

from pathlib import Path


def read_token(value: str | None, path: str | None) -> str | None:
    if value:
        return value.strip() or None
    if path:
        candidate = Path(path)
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8").strip() or None
    return None


__all__ = ["read_token"]
