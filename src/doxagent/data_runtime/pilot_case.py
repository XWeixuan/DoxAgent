"""Validation boundary for cryptographically bound local Pilot case roots."""

from __future__ import annotations

import json
from pathlib import Path

from doxagent.codex_runtime.schema import CodexD1Node


def validate_pilot_case_root(
    *,
    run_root: Path,
    pilot_case_id: str,
    run_id: str,
    attempt_id: str,
    node: CodexD1Node | None = None,
) -> dict[str, object]:
    resolved = run_root.resolve()
    if resolved.name != pilot_case_id:
        raise ValueError("Pilot case directory name does not match signed capability")
    manifest_path = resolved / "case_manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Pilot case manifest is missing or invalid") from exc
    if payload.get("schema_version") != "codex-d1-pilot-case-v1":
        raise ValueError("unsupported Pilot case manifest")
    if payload.get("case_id") != pilot_case_id or payload.get("run_id") != run_id:
        raise ValueError("Pilot case manifest scope mismatch")
    if payload.get("attempt_id") != attempt_id:
        raise ValueError("Pilot case attempt scope mismatch")
    if node is not None and payload.get("node") != node.value:
        raise ValueError("Pilot case node scope mismatch")
    return payload
