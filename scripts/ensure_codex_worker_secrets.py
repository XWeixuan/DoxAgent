"""Persist high-entropy local Codex worker secrets without printing their values."""

from __future__ import annotations

import secrets
from pathlib import Path

from dotenv import dotenv_values, set_key


def main() -> int:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    values = dotenv_values(env_path)
    changed: list[str] = []
    for key in (
        "DOXAGENT_CODEX_WORKER_BEARER_TOKEN",
        "DOXAGENT_CODEX_CAPABILITY_SECRET",
    ):
        current = values.get(key)
        if current and len(current.encode("utf-8")) >= 32:
            continue
        set_key(str(env_path), key, secrets.token_urlsafe(48), quote_mode="always")
        changed.append(key)
    print("Persistent Codex worker secrets ready.")
    print("Generated keys: " + (", ".join(changed) if changed else "none"))
    print("Secret values were not printed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
