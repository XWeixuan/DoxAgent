"""Assistant-friendly CLI for preparing, driving and exporting D2 SDK Pilots."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from doxagent.pilot.document2_case_builder import Document2PilotCaseBuilder
from doxagent.pilot.document2_coordinator import (
    Document2PilotCoordinator,
    Document2PilotCoordinatorEvent,
    Document2PilotCoordinatorRequest,
)
from doxagent.pilot.sdk_runner import (
    PilotSdkRunner,
    build_delivery,
    execution_lock,
    read_json,
    write_json,
)
from doxagent.workflows.codex_document2.schema import Document2Checkpoint


def coordinator_cases(state: dict) -> list[Path]:
    return [Path(stage["case_root"]) for stage in state["stages"] if stage.get("case_root")]


def _shell_thread(state: dict, case: Path) -> str | None:
    manifest = read_json(case / "case_manifest.json")
    if not manifest["node"].startswith("d2_o1_"):
        return None
    for previous in reversed(coordinator_cases(state)):
        if previous == case:
            continue
        other = read_json(previous / "case_manifest.json")
        if other["node"].startswith("d2_o1_") and other["run_id"] == manifest["run_id"]:
            attempt = other.get("node_attempt_id", other.get("attempt_id"))
            path = previous / "attempts" / attempt / "audit" / "pilot_sdk_receipt.json"
            if path.exists():
                receipt = read_json(path)
                if receipt["status"] == "completed":
                    return receipt["thread_id"]
    return None


async def drive(
    coordinator: Document2PilotCoordinator,
    runner: PilotSdkRunner,
    coordinator_id: str,
    *,
    coordinator_root: Path,
    initial: Document2PilotCoordinatorEvent | None = None,
    shell_key: str | None = None,
    max_nodes: int | None = None,
    stop_after_node: str | None = None,
    retry: bool = False,
) -> dict:
    if max_nodes is not None and max_nodes < 1:
        raise ValueError("max_nodes must be positive")
    with execution_lock(coordinator_root / ".pilot-driver.lock"):
        return await _drive(
            coordinator,
            runner,
            coordinator_id,
            coordinator_root=coordinator_root,
            initial=initial,
            shell_key=shell_key,
            max_nodes=max_nodes,
            stop_after_node=stop_after_node,
            retry=retry,
        )


async def _drive(
    coordinator,
    runner,
    coordinator_id,
    *,
    coordinator_root,
    initial,
    shell_key,
    max_nodes,
    stop_after_node,
    retry,
) -> dict:
    status = "failed"
    completed = 0
    try:
        event = initial
        while True:
            state = coordinator.status(coordinator_id)
            if event is None:
                active = next((s for s in state["stages"] if s["status"] == "active"), None)
                if active:
                    # Never allow legacy JSON-only readiness to bypass SDK execution/review.
                    event = Document2PilotCoordinatorEvent(
                        status="waiting",
                        coordinator_root=coordinator_root,
                        case_root=Path(active["case_root"]),
                    )
                else:
                    event = await coordinator.advance(coordinator_id, shell_key=shell_key)
            if event.status in {"completed", "selection_required"}:
                status = event.status
                break
            if event.case_root is None:
                raise RuntimeError("Pilot coordinator did not provide an executable case")
            await runner.run_case(
                event.case_root,
                thread_id=_shell_thread(state, event.case_root),
                retry=retry,
            )
            completed += 1
            manifest = read_json(event.case_root / "case_manifest.json")
            if manifest["node"] == stop_after_node:
                status = "scope_completed"
                break
            event = await coordinator.advance(coordinator_id, shell_key=shell_key)
            if max_nodes is not None and completed >= max_nodes:
                status = (
                    event.status
                    if event.status in {"completed", "selection_required"}
                    else "paused"
                )
                break
    finally:
        state = coordinator.status(coordinator_id)
        write_json(
            coordinator_root / "pilot_driver_receipt.json",
            {
                "status": status,
                "nodes_executed": completed,
            },
        )
        delivery = build_delivery(coordinator_root, coordinator_cases(state), status=status)
    delivery.update(nodes_executed=completed)
    if status == "selection_required":
        delivery["selection_required"] = state.get("selection_required")
    return delivery


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    case = commands.add_parser("case", help="Execute an already prepared case")
    case.add_argument("--case-root", type=Path, required=True)
    for name in ("start", "continue"):
        command = commands.add_parser(name)
        command.add_argument("--coordinator-id", required=True)
        command.add_argument("--cases-root", type=Path, default=Path("D:/DoxAgentPilot/cases"))
        command.add_argument(
            "--coordinators-root", type=Path, default=Path("D:/DoxAgentPilot/coordinators")
        )
        command.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[3])
        command.add_argument("--runtime-env-file", type=Path, required=True)
        command.add_argument(
            "--source-snapshot-db",
            type=Path,
            help="Local SQLite snapshot of a verified published source run",
        )
        command.add_argument("--event-library-version", type=int)
        command.add_argument("--shell-key")
        command.add_argument("--max-nodes", type=int)
        command.add_argument("--stop-after-node")
        if name == "start":
            command.add_argument("--source-d2-run-id")
            command.add_argument("--source-global-run-id")
            command.add_argument("--checkpoint", type=Path)
            command.add_argument(
                "--document-schema-version",
                default="document2.v2.1",
                choices=["document2.v2", "document2.v2.1"],
            )
            command.add_argument("--capability-hours", type=int, default=24 * 365 * 10)
    for command in (case, *[commands.choices[name] for name in ("start", "continue")]):
        command.add_argument("--model")
        command.add_argument("--effort", choices=["low", "medium", "high", "xhigh"])
        command.add_argument("--timeout-seconds", type=float, default=7200)
        command.add_argument("--retry", action="store_true")
    report = commands.add_parser("report", help="Rebuild delivery without any model call")
    report.add_argument("--coordinator-root", type=Path, required=True)
    return parser


async def _execute(args: argparse.Namespace) -> dict:
    if args.command == "report":
        state = read_json(args.coordinator_root / "coordinator_state.json")
        driver = args.coordinator_root / "pilot_driver_receipt.json"
        return build_delivery(
            args.coordinator_root,
            coordinator_cases(state),
            status=read_json(driver)["status"] if driver.exists() else state["status"],
        )
    if args.command in {"start", "continue"}:
        load_dotenv(args.runtime_env_file, override=True)
    runner = PilotSdkRunner(
        model=args.model, effort=args.effort, timeout_seconds=args.timeout_seconds
    )
    coordinator = None
    try:
        if args.command == "case":
            status = "failed"
            try:
                await runner.run_case(args.case_root, retry=args.retry)
                status = "completed"
            finally:
                delivery = build_delivery(
                    args.case_root / "pilot_delivery", [args.case_root], status=status
                )
            return delivery
        if args.source_snapshot_db is not None:
            from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository

            source_repository = SQLiteCodexRuntimeRepository(args.source_snapshot_db)
        else:
            source_repository = None
        builder = Document2PilotCaseBuilder(
            repo_root=args.repo_root,
            cases_root=args.cases_root,
            python=sys.executable,
            runtime_env_file=args.runtime_env_file,
            repository=source_repository,
            event_library_version=args.event_library_version,
        )
        coordinator = Document2PilotCoordinator(
            builder=builder, coordinators_root=args.coordinators_root
        )
        initial = None
        if args.command == "start":
            checkpoint = (
                Document2Checkpoint.model_validate(read_json(args.checkpoint))
                if args.checkpoint
                else None
            )
            source_d2_run_id = args.source_d2_run_id or (
                checkpoint.run_id if checkpoint else f"d2pilot-{args.coordinator_id}"
            )
            if checkpoint is None and args.source_global_run_id is None:
                from doxagent.codex_runtime.repository import SQLiteCodexRuntimeRepository
                from doxagent.settings import DoxAgentSettings
                from doxagent.workflows.codex_document2.schema import Document2Bundle

                repository = SQLiteCodexRuntimeRepository(
                    DoxAgentSettings().codex_runtime_sqlite_path
                )
                bundle = repository.get_bundle(source_d2_run_id)
                if not isinstance(bundle, Document2Bundle) or bundle.checkpoint is None:
                    raise ValueError("Source D2 checkpoint unavailable; supply --checkpoint")
                checkpoint = bundle.checkpoint
            initial = await coordinator.start(
                Document2PilotCoordinatorRequest(
                    coordinator_id=args.coordinator_id,
                    source_d2_run_id=source_d2_run_id,
                    source_global_run_id=args.source_global_run_id,
                    checkpoint=checkpoint,
                    shell_key=args.shell_key,
                    document_schema_version=args.document_schema_version,
                    capability_hours=args.capability_hours,
                )
            )
        return await drive(
            coordinator,
            runner,
            args.coordinator_id,
            coordinator_root=args.coordinators_root / args.coordinator_id,
            initial=initial,
            shell_key=args.shell_key,
            max_nodes=args.max_nodes,
            stop_after_node=args.stop_after_node,
            retry=args.retry,
        )
    finally:
        await runner.aclose()
        if coordinator is not None:
            await coordinator.aclose()


def main() -> None:
    args = _parser().parse_args()
    try:
        result = asyncio.run(_execute(args))
    except Exception as exc:
        delivery_root = (
            args.case_root / "pilot_delivery"
            if args.command == "case"
            else args.coordinators_root / args.coordinator_id
            if args.command in {"start", "continue"}
            else args.coordinator_root
        )
        result = {
            "status": "failed",
            "error_type": type(exc).__name__,
            "message": "Execution failed; inspect node receipt/report for details.",
        }
        for key, filename in (("report", "PILOT_REPORT.md"), ("artifacts", "pilot_delivery.zip")):
            if (delivery_root / filename).exists():
                result[key] = str((delivery_root / filename).resolve())
        # SDK exceptions may contain config values; detailed errors are redacted in receipts.
        print(json.dumps(result, ensure_ascii=False))
        raise SystemExit(1) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
