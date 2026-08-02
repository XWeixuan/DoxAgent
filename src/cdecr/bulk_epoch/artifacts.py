"""Content-addressed stage artifacts and stable task identifiers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any


def canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str
        ).encode("utf-8")
    ).hexdigest()


def stable_task_id(stage: str, payload: object) -> str:
    return f"{stage.casefold()}:{canonical_hash(payload)[:24]}"


@dataclass(frozen=True)
class StageArtifact:
    kind: str
    manifest_hash: str
    upstream_hash: str
    engine_version: str
    payload: dict[str, Any]

    @property
    def artifact_hash(self) -> str:
        return canonical_hash(
            {
                "kind": self.kind,
                "manifest_hash": self.manifest_hash,
                "upstream_hash": self.upstream_hash,
                "engine_version": self.engine_version,
                "payload": self.payload,
            }
        )
