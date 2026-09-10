"""Commit SDK completion before notifying the controller across the process pipe."""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

from .schema import WorkerRunRequest


def receipt_path(root: Path, request: WorkerRunRequest) -> Path:
    identity = request.run_id + ":" + (request.idempotency_key or request.attempt_id)
    return root / "worker-results" / (hashlib.sha256(identity.encode()).hexdigest() + ".json")


def commit(path: Path, result: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(result, file, ensure_ascii=False)
        file.flush()
        os.fsync(file.fileno())
    temporary.replace(path)
    if os.name == "posix":
        descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
