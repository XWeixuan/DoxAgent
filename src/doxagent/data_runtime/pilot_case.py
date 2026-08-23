"""Validation boundary for cryptographically bound local Pilot case roots."""

from __future__ import annotations

import json
from pathlib import Path
from typing import cast

from doxagent.codex_runtime.schema import CodexResearchNode


def canonical_node_attempt_id(payload: dict[str, object]) -> str:
    """Resolve the canonical attempt scope and enforce the legacy storage alias."""

    canonical = payload.get("node_attempt_id")
    legacy = payload.get("attempt_id")
    if canonical is not None and legacy is not None and canonical != legacy:
        raise ValueError("Pilot case node_attempt_id and legacy attempt_id disagree")
    value = canonical if canonical is not None else legacy
    if not isinstance(value, str) or not value:
        raise ValueError("Pilot case node_attempt_id is missing")
    return value


def validate_pilot_case_root(
    *,
    run_root: Path,
    pilot_case_id: str,
    run_id: str,
    attempt_id: str,
    node: CodexResearchNode | None = None,
) -> dict[str, object]:
    resolved = run_root.resolve()
    if resolved.name != pilot_case_id:
        raise ValueError("Pilot case directory name does not match signed capability")
    manifest_path = resolved / "case_manifest.json"
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("Pilot case manifest is missing or invalid") from exc
    if payload.get("schema_version") not in {
        "codex-d1-pilot-case-v1",
        "codex-research-pilot-case-v2",
    }:
        raise ValueError("unsupported Pilot case manifest")
    if payload.get("case_id") != pilot_case_id or payload.get("run_id") != run_id:
        raise ValueError("Pilot case manifest scope mismatch")
    if canonical_node_attempt_id(payload) != attempt_id:
        raise ValueError("Pilot case attempt scope mismatch")
    if node is not None and payload.get("node") != node.value:
        raise ValueError("Pilot case node scope mismatch")
    return cast(dict[str, object], payload)
