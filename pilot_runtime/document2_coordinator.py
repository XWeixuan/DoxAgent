"""Persistent CLI for the Pilot-only Document2 case coordinator."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv


def main() -> int:
    runtime_root = Path(__file__).resolve().parent
    load_dotenv(runtime_root / ".env.local", override=True)
    repo_root = Path(os.environ["DOXAGENT_PILOT_REPO_ROOT"]).resolve()
    sys.path.insert(0, str(repo_root / "src"))
    return asyncio.run(_main(repo_root))


async def _main(repo_root: Path) -> int:
    from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
    from doxagent.pilot.document2_case_builder import Document2PilotCaseBuilder
    from doxagent.pilot.document2_coordinator import (
        Document2PilotCoordinator,
        Document2PilotCoordinatorRequest,
    )
    from doxagent.settings import DoxAgentSettings
    from doxagent.workflows.codex_document2.schema import Document2Bundle

    parser = argparse.ArgumentParser(
        description="Create and advance one dependency-aware Document2 Pilot case at a time"
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    start = subparsers.add_parser("start")
    source = start.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--source-d2-run",
        help="Existing successful D2 run used as an exact source-attempt fixture",
    )
    source.add_argument(
        "--source-global-run",
        help="Published Global Research run used to bootstrap the first D2 Pilot",
    )
    start.add_argument("--coordinator-id", required=True)
    start.add_argument("--shell", help="Shell map key or shell_id; required when the run has many")
    start.add_argument("--capability-hours", type=int, default=24 * 365 * 10)
    start.add_argument("--poll-seconds", type=float, default=5.0)
    start.add_argument("--no-watch", action="store_true")
    for command in ("advance", "watch", "status"):
        child = subparsers.add_parser(command)
        child.add_argument("--coordinator-id", required=True)
        if command == "advance":
            child.add_argument(
                "--shell",
                help="Select a shell_id after bootstrap finalization produced many shells",
            )
            child.add_argument(
                "--finalized-shells",
                type=Path,
                help="Validated ShellFinalizationResult JSON replacing the O0 handoff for O1",
            )
        if command == "watch":
            child.add_argument("--poll-seconds", type=float, default=5.0)
    args = parser.parse_args()

    settings = DoxAgentSettings()
    cases_root = Path(os.environ["DOXAGENT_PILOT_CASES_ROOT"])
    builder = Document2PilotCaseBuilder(
        repo_root=repo_root,
        cases_root=cases_root,
        python=Path(os.environ["DOXAGENT_PILOT_PYTHON"]),
        runtime_env_file=Path(os.environ["DOXAGENT_PILOT_ENV_FILE"]),
        settings=settings,
    )
    coordinator = Document2PilotCoordinator(
        builder=builder,
        coordinators_root=cases_root / "document2" / "_coordinators",
    )
    try:
        if args.command == "status":
            print(json.dumps(coordinator.status(args.coordinator_id), ensure_ascii=False, indent=2))
            return 0
        if args.command == "start":
            if args.source_d2_run is not None:
                repository = SQLiteCodexRuntimeRepository(settings.codex_runtime_sqlite_path)
                bundle = repository.get_bundle(args.source_d2_run)
                if not isinstance(bundle, Document2Bundle) or bundle.checkpoint is None:
                    raise ValueError(
                        "source Document2 run/checkpoint is unavailable in the local runtime SQLite"
                    )
                source_d2_run_id = args.source_d2_run
                checkpoint = bundle.checkpoint
                source_global_run_id = bundle.source_global_run_id
            else:
                source_d2_run_id = f"d2pilot-{args.coordinator_id}"
                checkpoint = None
                source_global_run_id = args.source_global_run
            event = await coordinator.start(
                Document2PilotCoordinatorRequest(
                    coordinator_id=args.coordinator_id,
                    source_d2_run_id=source_d2_run_id,
                    checkpoint=checkpoint,
                    source_global_run_id=source_global_run_id,
                    shell_key=args.shell,
                    capability_hours=args.capability_hours,
                )
            )
            _print_event(event)
            if args.no_watch:
                return 0
            return await _watch_all(coordinator, args.coordinator_id, args.poll_seconds)
        if args.command == "advance":
            event = await coordinator.advance(
                args.coordinator_id,
                shell_key=args.shell,
                finalized_shells_path=args.finalized_shells,
            )
            _print_event(event)
            return 2 if event.status == "selection_required" else 0
        return await _watch_all(coordinator, args.coordinator_id, args.poll_seconds)
    finally:
        await coordinator.aclose()


async def _watch_all(coordinator: object, coordinator_id: str, poll_seconds: float) -> int:
    while True:
        event = await coordinator.watch(coordinator_id, poll_seconds=poll_seconds)  # type: ignore[attr-defined]
        _print_event(event)
        if event.status == "selection_required":
            return 2
        if event.status == "completed":
            return 0


def _print_event(event: object) -> None:
    print(
        json.dumps(
            {
                "status": event.status,  # type: ignore[attr-defined]
                "coordinator_root": str(event.coordinator_root),  # type: ignore[attr-defined]
                "node": event.node.value if event.node is not None else None,  # type: ignore[attr-defined]
                "case_root": str(event.case_root) if event.case_root is not None else None,  # type: ignore[attr-defined]
                "task_path": str(event.task_path) if event.task_path is not None else None,  # type: ignore[attr-defined]
                "available_shell_ids": list(event.available_shell_ids),  # type: ignore[attr-defined]
                "message": event.message,  # type: ignore[attr-defined]
            },
            ensure_ascii=False,
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    raise SystemExit(main())
