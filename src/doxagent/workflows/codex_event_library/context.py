"""Seed the six immutable O2 attempt inputs without duplicating Frozen View payloads."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.event_library.contracts import FrozenViewManifest
from doxagent.workflows.codex_event_library.schema import O2RunResult, PreparedO2Attempt


class EventLibraryAttemptSeeder:
    def __init__(
        self,
        workspace: LocalWorkspaceStore,
        prompt_root: str | Path = "prompts/codex_v2/event_library",
    ) -> None:
        self._workspace = workspace
        self._prompt_root = Path(prompt_root)

    def seed(
        self,
        *,
        run_id: str,
        attempt_id: str,
        manifest: FrozenViewManifest,
        skill_asset: str = "skills/foundation.md",
        previous_failure: str | None = None,
        stage: str = "CANONICAL_EDIT",
        assigned_delta_ids: list[str] | None = None,
        prior_attempt_paths: list[str] | None = None,
        task_metadata: dict[str, Any] | None = None,
    ) -> PreparedO2Attempt:
        self._workspace.ensure_attempt(run_id, attempt_id)
        prefix = f"attempts/{attempt_id}/input"
        assets = {
            "AGENTS.md": (self._prompt_root / "AGENTS.md").read_text(encoding="utf-8"),
            "agent.md": (self._prompt_root / "agents" / "o2.md").read_text(encoding="utf-8"),
            "skill.md": (self._prompt_root / skill_asset).read_text(encoding="utf-8"),
            "task.json": json.dumps(
                {
                    "workflow": "codex_event_library_v1",
                    "attempt_id": attempt_id,
                    "mode": manifest.mode,
                    "stage": stage,
                    "required_input_order": [
                        "AGENTS.md",
                        "agent.md",
                        "skill.md",
                        "task.json",
                        "context.json",
                        "output_schema.json",
                    ],
                    "frozen_view_manifest": (
                        f"context/event_library/{manifest.frozen_view_id}/manifest.json"
                    ),
                    "output_bundle_path": f"attempts/{attempt_id}/output/revision_bundle",
                    "assigned_delta_ids": assigned_delta_ids or [],
                    "prior_attempt_paths": prior_attempt_paths or [],
                    "previous_failure": previous_failure,
                    "metadata": task_metadata or {},
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            "context.json": manifest.model_dump_json(indent=2) + "\n",
            "output_schema.json": json.dumps(
                O2RunResult.model_json_schema(), ensure_ascii=False, indent=2
            )
            + "\n",
        }
        for name, content in assets.items():
            self._workspace.write_text(run_id, f"{prefix}/{name}", content)
        run_root = self._workspace.ensure_run(run_id)
        (run_root / "attempts" / attempt_id / "output" / "work").mkdir(exist_ok=True)
        (run_root / "attempts" / attempt_id / "output" / "revision_bundle").mkdir(exist_ok=True)
        return PreparedO2Attempt(
            run_id=run_id,
            attempt_id=attempt_id,
            frozen_view_id=manifest.frozen_view_id,
            frozen_view_path=f"context/event_library/{manifest.frozen_view_id}",
            input_paths=[f"{prefix}/{name}" for name in assets],
            output_bundle_path=f"attempts/{attempt_id}/output/revision_bundle",
        )
