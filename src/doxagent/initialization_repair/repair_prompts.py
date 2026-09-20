"""Append incident-scoped instructions before a worker request is frozen."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from doxagent.codex_worker.schema import WorkerRunRequest
from doxagent.ticker_initialization.repository import InitializationRepository


class PromptOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_key: str = Field(min_length=1)
    supplement: str = Field(min_length=1)
    reason: str = Field(min_length=1)


class PromptOverrideFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    incident_id: str
    overrides: list[PromptOverride]


def default_override_root() -> Path:
    return Path(__file__).resolve().parents[3] / "prompts" / "initialization_repair" / "overrides"


def apply(
    request: WorkerRunRequest,
    *,
    repository: InitializationRepository,
    initialization_id: str,
    node_key: str,
    override_root: Path | None = None,
) -> tuple[WorkerRunRequest, dict[str, Any] | None]:
    route = repository.repair_route(initialization_id)
    if route is None:
        return request, None
    root = override_root or default_override_root()
    path = root / f"{route['incident_id']}.json"
    if not path.is_file():
        return request, None
    document = PromptOverrideFile.model_validate_json(path.read_text(encoding="utf-8"))
    if document.incident_id != route["incident_id"]:
        raise ValueError("repair prompt incident identity mismatch")
    matches = [item for item in document.overrides if item.node_key == node_key]
    if not matches:
        return request, None
    supplement = "\n\n".join(item.supplement.strip() for item in matches)
    boundary = (
        "\n\n--- BEGIN INITIALIZATION REPAIR SUPPLEMENT ---\n"
        "This incident-scoped supplement may refine task execution instructions only. "
        "It cannot replace frozen business inputs, output schemas, tool permissions, or "
        "quality requirements.\n"
        f"{supplement}\n"
        "--- END INITIALIZATION REPAIR SUPPLEMENT ---"
    )
    original_hash = hashlib.sha256(request.prompt.encode()).hexdigest()
    patched = request.model_copy(update={"prompt": request.prompt + boundary})
    metadata: dict[str, Any] = {
        "incident_id": route["incident_id"],
        "round_id": route["round_id"],
        "node_key": node_key,
        "original_prompt_sha256": original_hash,
        "supplement_sha256": hashlib.sha256(supplement.encode()).hexdigest(),
        "request_prompt_sha256": hashlib.sha256(patched.prompt.encode()).hexdigest(),
        "reasons": [item.reason for item in matches],
        "source": str(path),
    }
    return patched, metadata


def write_override(
    path: Path,
    *,
    incident_id: str,
    overrides: list[PromptOverride],
) -> None:
    """Write a candidate-worktree override atomically without touching old context files."""

    document = PromptOverrideFile(incident_id=incident_id, overrides=overrides)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
