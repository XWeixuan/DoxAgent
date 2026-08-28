"""Seed the six immutable O2 attempt inputs without duplicating Frozen View payloads."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.event_library.contracts import FrozenViewManifest
from doxagent.workflows.codex_event_library.schema import (
    O2_RUN_RESULT_SCHEMA,
    PreparedO2Attempt,
)

CONTROL_INPUT_ORDER = [
    "AGENTS.md",
    "agent.md",
    "skill.md",
    "context.json",
    "output_schema.json",
]


def build_attempt_assets(
    *,
    prompt_root: str | Path,
    manifest: FrozenViewManifest,
    attempt_id: str,
    stage: str,
    skill_asset: str,
    assigned_delta_ids: list[str] | None = None,
    prior_attempt_paths: list[str] | None = None,
    previous_failure: str | None = None,
    task_metadata: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Build the one canonical six-file attempt input set."""

    root = Path(prompt_root)
    foundation_path = root / "skills" / "foundation.md"
    stage_path = root / skill_asset
    foundation = foundation_path.read_text(encoding="utf-8").strip()
    stage_content = stage_path.read_text(encoding="utf-8").strip()
    if stage_path.resolve() == foundation_path.resolve():
        combined_skill = f"# Foundation Contract\n\n{foundation}\n"
    else:
        combined_skill = (
            f"# Foundation Contract\n\n{foundation}\n\n"
            f"# Current Stage Contract\n\n{stage_content}\n"
        )
    metadata = dict(task_metadata or {})
    task = {
        "workflow": "codex_event_library_v1",
        "attempt_id": attempt_id,
        "mode": manifest.mode,
        "stage": stage,
        "content_input_order": list(CONTROL_INPUT_ORDER),
        "frozen_view_manifest": (f"context/event_library/{manifest.frozen_view_id}/manifest.json"),
        "frozen_as_of": manifest.as_of.isoformat(),
        "required_bundle_identity": metadata.pop(
            "required_bundle_identity",
            {
                "contract_version": "event-library-maintenance-v3",
                "run_id": manifest.run_id,
                "ticker": manifest.ticker,
                "base_library_version": manifest.base_library_version,
                "delta_batch_ids": list(manifest.delta_batch_ids),
            },
        ),
        "assigned_delta_ids": assigned_delta_ids or [],
        "prior_attempt_paths": prior_attempt_paths or [],
        "required_frozen_paths": metadata.pop("required_frozen_paths", []),
        "allowed_event_detail_ids": metadata.pop("allowed_event_detail_ids", []),
        "event_detail_paths": metadata.pop("event_detail_paths", {}),
        "reference_review_policy": metadata.pop("reference_review_policy", None),
        "deterministic_review_fields_by_event": metadata.pop(
            "deterministic_review_fields_by_event", {}
        ),
        "work_path": f"attempts/{attempt_id}/output/work",
        "date_resolution_ledger_path": (
            f"attempts/{attempt_id}/output/work/date_resolution_ledger.jsonl"
        ),
        "reference_view_decision_ledger_path": (
            f"attempts/{attempt_id}/output/work/reference_view_decision_ledger.jsonl"
        ),
        "output_bundle_path": f"attempts/{attempt_id}/output/revision_bundle",
        "bundle_ledger_paths": {
            "date_resolution": (
                f"attempts/{attempt_id}/output/revision_bundle/date_resolution_ledger.jsonl"
            ),
            "reference_view_decision": (
                "attempts/"
                f"{attempt_id}/output/revision_bundle/reference_view_decision_ledger.jsonl"
            ),
        },
        "previous_failure": previous_failure,
        "prompt_manifest": {
            "foundation_source": "skills/foundation.md",
            "foundation_sha256": hashlib.sha256((foundation + "\n").encode("utf-8")).hexdigest(),
            "stage_source": skill_asset,
            "stage_sha256": hashlib.sha256((stage_content + "\n").encode("utf-8")).hexdigest(),
            "combined_skill_sha256": hashlib.sha256(combined_skill.encode("utf-8")).hexdigest(),
        },
        "metadata": metadata,
    }
    return {
        "AGENTS.md": (root / "AGENTS.md").read_text(encoding="utf-8"),
        "agent.md": (root / "agents" / "o2.md").read_text(encoding="utf-8"),
        "skill.md": combined_skill,
        "task.json": json.dumps(task, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        "context.json": manifest.model_dump_json(indent=2) + "\n",
        "output_schema.json": json.dumps(O2_RUN_RESULT_SCHEMA, ensure_ascii=False, indent=2) + "\n",
    }


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
        assets = build_attempt_assets(
            prompt_root=self._prompt_root,
            manifest=manifest,
            attempt_id=attempt_id,
            stage=stage,
            skill_asset=skill_asset,
            assigned_delta_ids=assigned_delta_ids,
            prior_attempt_paths=prior_attempt_paths,
            previous_failure=previous_failure,
            task_metadata=task_metadata,
        )
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
