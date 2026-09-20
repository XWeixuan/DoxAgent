"""Small permission-restricted credential store owned by Site Access."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


class CredentialStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            self.root.chmod(0o700)
        except OSError:
            pass

    def put(self, credential_id: str, *, username: str, password: str) -> None:
        path = self._path(credential_id)
        if not username or not password:
            raise ValueError("username and password are required")
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        descriptor = os.open(path, flags, 0o600)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"username": username, "password": password}, stream)
                stream.write("\n")
            path.chmod(0o600)
        except Exception:
            try:
                os.close(descriptor)
            except OSError:
                pass
            raise

    def get(self, credential_id: str) -> dict[str, str]:
        payload = json.loads(self._path(credential_id).read_text(encoding="utf-8"))
        username = str(payload.get("username", ""))
        password = str(payload.get("password", ""))
        if not username or not password:
            raise ValueError("credential is incomplete")
        return {"username": username, "password": password}

    def status(self, credential_id: str) -> dict[str, object]:
        path = self._path(credential_id)
        if not path.exists():
            return {"credential_id": credential_id, "available": False}
        payload = self.get(credential_id)
        username = payload["username"]
        label = username[:2] + "…" + username[-2:] if len(username) > 5 else "***"
        return {"credential_id": credential_id, "available": True, "username_label": label}

    def _path(self, credential_id: str) -> Path:
        value = credential_id.strip().lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", value):
            raise ValueError("invalid credential id")
        return self.root / f"{value}.json"


__all__ = ["CredentialStore"]
