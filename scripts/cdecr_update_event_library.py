"""Prepare or deterministically import a Canonical Event Library Bundle without model coupling."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from doxagent.codex_worker.workspace_store import LocalWorkspaceStore
from doxagent.event_library.contracts import FrozenRuntimeSnapshot
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.event_library.service import EventLibraryService
from doxagent.workflows.codex_event_library.orchestrator import (
    EventLibraryFoundationOrchestrator,
)
from doxagent.workflows.codex_event_library.runner import EventLibraryAgentRunner


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-snapshot", type=Path, required=True)
    parser.add_argument("--event-library", type=Path, required=True)
    parser.add_argument("--workspace-root", type=Path, required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--attempt-id", default="o2-attempt-1")
    parser.add_argument("--mode", choices=("INITIALIZE", "INCREMENTAL"), default="INITIALIZE")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--import-bundle", type=Path)
    parser.add_argument("--export-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    snapshot = FrozenRuntimeSnapshot.model_validate_json(
        args.runtime_snapshot.read_text(encoding="utf-8")
    )
    repository = EventLibraryRepository(args.event_library)
    service = EventLibraryService(repository)
    workspace = LocalWorkspaceStore(args.workspace_root)
    runner = EventLibraryAgentRunner(workspace=workspace, repository=repository)
    orchestrator = EventLibraryFoundationOrchestrator(service=service, agent_runner=runner)
    batch, frozen_root, manifest = orchestrator.prepare(
        snapshot=snapshot,
        run_id=args.run_id,
        attempt_id=args.attempt_id,
        mode=args.mode,
    )
    if not batch.items:
        print(
            json.dumps(
                {"status": "FINALIZED_NOOP", "batch_id": batch.batch_id},
                ensure_ascii=False,
            )
        )
        return 0
    prepared = {
        "status": "PREPARED",
        "batch_id": batch.batch_id,
        "frozen_view_id": manifest.frozen_view_id if manifest else None,
        "frozen_view_path": str(frozen_root) if frozen_root else None,
    }
    if args.prepare_only or args.import_bundle is None:
        print(json.dumps(prepared, ensure_ascii=False))
        return 0
    export_dir = args.export_dir or (args.workspace_root / args.run_id / "published_exports")
    result, outcome, exports = orchestrator.import_promoted_or_reviewed_bundle(
        run_id=args.run_id,
        bundle_path=args.import_bundle,
        export_dir=export_dir,
    )
    print(
        json.dumps(
            {
                **prepared,
                "status": "PUBLISHED",
                "publication": result.model_dump(mode="json"),
                "validation": outcome.model_dump(mode="json", exclude={"normalized_bundle"}),
                "exports": {key: str(value) for key, value in exports.items()},
            },
            ensure_ascii=False,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
