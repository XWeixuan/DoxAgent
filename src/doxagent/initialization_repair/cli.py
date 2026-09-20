"""Operator and container entry points for initialization repair."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.repository import InitializationRepository
from doxagent.trade_execution.worker import WriterLock
from doxagent.v2_control.repository import ControlRepository

from .agent import RepairAgent
from .containers import DockerRuntime
from .guardian import Guardian
from .issues import render_to
from .repository import RepairRepository


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="doxagent-initialization-repair")
    commands = root.add_subparsers(dest="command", required=True)
    guardian = commands.add_parser("guardian")
    guardian.add_argument("--initialization-db", required=True, type=Path)
    guardian.add_argument("--runtime-db", required=True, type=Path)
    guardian.add_argument("--repair-root", type=Path)
    guardian.add_argument("--source-repository")
    guardian.add_argument("--production-container")
    guardian.add_argument("--agent-image")
    guardian.add_argument("--codex-auth-file", type=Path)
    guardian.add_argument("--once", action="store_true")

    agent = commands.add_parser("agent")
    agent.add_argument("--incident-id", required=True)
    agent.add_argument("--round-id", required=True)
    agent.add_argument("--worktree", required=True, type=Path)
    agent.add_argument("--context", required=True, type=Path)
    agent.add_argument("--receipt", required=True, type=Path)
    agent.add_argument("--report", required=True, type=Path)
    agent.add_argument("--codex-home", required=True, type=Path)
    agent.add_argument("--thread-id")
    agent.add_argument("--developer-prompt", type=Path)

    for name in ("status", "inspect", "adopt", "release", "issues"):
        command = commands.add_parser(name)
        command.add_argument("--initialization-db", required=True, type=Path)
        if name in {"inspect", "release"}:
            command.add_argument("--incident-id", required=True)
        if name == "adopt":
            command.add_argument("--initialization-id", required=True)
            command.add_argument("--runtime-db", required=True, type=Path)
            command.add_argument("--repair-root", type=Path)
            command.add_argument("--source-repository")
            command.add_argument("--production-container")
            command.add_argument("--agent-image")
            command.add_argument("--codex-auth-file", type=Path)
        if name == "release":
            command.add_argument("--reason", required=True)
        if name == "issues":
            command.add_argument("--output", required=True, type=Path)
    return root


def _guardian(args: argparse.Namespace, settings: DoxAgentSettings) -> Guardian:
    initialization = InitializationRepository(args.initialization_db)
    control = ControlRepository(RuntimeJournal(args.runtime_db, initialize=False))
    repair_root = args.repair_root or Path(settings.initialization_repair_root)
    source = args.source_repository or settings.initialization_repair_source_repository
    if not source:
        raise ValueError("repair source repository is required")
    return Guardian(
        initialization,
        control,
        repair_root=repair_root,
        source_repository=source,
        production_container=(
            args.production_container or settings.initialization_repair_production_container
        ),
        agent_image=args.agent_image or settings.initialization_repair_agent_image,
        codex_auth_file=(
            args.codex_auth_file
            or (
                Path(settings.initialization_repair_codex_auth_file)
                if settings.initialization_repair_codex_auth_file
                else None
            )
        ),
        docker=DockerRuntime(settings.initialization_repair_docker_executable),
    )


async def _run_agent(args: argparse.Namespace) -> int:
    developer_prompt = args.developer_prompt or (
        args.worktree / "prompts" / "initialization_repair" / "agent.md"
    )
    report, thread_id, turn_id = await RepairAgent(
        developer_prompt=developer_prompt,
        codex_home=args.codex_home,
    ).run(
        incident_id=args.incident_id,
        round_id=args.round_id,
        worktree=args.worktree,
        context_path=args.context,
        receipt_path=args.receipt,
        report_path=args.report,
        thread_id=args.thread_id,
    )
    print(
        json.dumps(
            {
                "thread_id": thread_id,
                "turn_id": turn_id,
                "report": report.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    settings = DoxAgentSettings()
    if args.command == "agent":
        return asyncio.run(_run_agent(args))
    if args.command == "guardian":
        if not settings.initialization_repair_enabled and not args.once:
            raise ValueError("DOXAGENT_INITIALIZATION_REPAIR_ENABLED is false")
        guardian = _guardian(args, settings)
        lock = (args.repair_root or Path(settings.initialization_repair_root)) / "guardian"
        with WriterLock(lock):
            while True:
                guardian.tick()
                if args.once:
                    return 0
                time.sleep(settings.initialization_repair_scan_seconds)
    initialization = InitializationRepository(args.initialization_db)
    repairs = RepairRepository(initialization)
    if args.command == "status":
        print(
            json.dumps(
                [item.model_dump(mode="json") for item in repairs.incidents()],
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "inspect":
        incident = repairs.get(args.incident_id)
        print(
            json.dumps(
                {
                    "incident": incident.model_dump(mode="json"),
                    "rounds": [
                        item.model_dump(mode="json")
                        for item in repairs.rounds(incident.incident_id)
                    ],
                    "budgets": [
                        item.model_dump(mode="json")
                        for item in repairs.budgets(incident.incident_id)
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif args.command == "adopt":
        print(_guardian(args, settings).adopt(args.initialization_id).model_dump_json(indent=2))
    elif args.command == "release":
        incident = repairs.get(args.incident_id)
        if incident.current_round_id:
            docker = DockerRuntime(settings.initialization_repair_docker_executable)
            for role in ("agent", "verify", "exec"):
                name = f"doxagent-repair-{role}-{incident.current_round_id}"
                state = docker.state(name)
                if state is not None and state.status == "running":
                    raise ValueError(f"REPAIR_CONTAINER_ACTIVE:{name}")
        print(repairs.release(args.incident_id, reason=args.reason).model_dump_json(indent=2))
    elif args.command == "issues":
        render_to(repairs, args.output)
        print(str(args.output.resolve()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
