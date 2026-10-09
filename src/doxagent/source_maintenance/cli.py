"""Explicit opt-in CLI. Default guardian exits without creating state or calling Docker."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path

from .schema import HealthSample, Incident
from .settings import MaintenanceSettings


def parser():
    root = argparse.ArgumentParser(prog="doxagent-source-maintenance")
    root.add_argument("--root", type=Path)
    commands = root.add_subparsers(dest="command", required=True)
    guardian = commands.add_parser("guardian")
    guardian.add_argument("--once", action="store_true")
    for name in [
        "status",
        "inspect",
        "adopt",
        "retry",
        "cancel",
        "issues",
        "replay",
        "evidence-server",
    ]:
        cmd = commands.add_parser(name)
        if name in {"inspect", "retry", "cancel"}:
            cmd.add_argument("incident_id")
        if name in {"retry", "cancel"}:
            cmd.add_argument("--reason", required=True)
        if name in {"adopt", "replay"}:
            cmd.add_argument("file", type=Path)
    agent = commands.add_parser("agent")
    agent.add_argument("--worktree", type=Path, required=True)
    agent.add_argument("--round-id", required=True)
    agent.add_argument("--thread-id")
    agent.add_argument("--model", default="gpt-6.1-sol")
    agent.add_argument("--effort", default="medium")
    agent.add_argument(
        "--prompt",
        type=Path,
        default=Path("/opt/maintenance/prompts/source_maintenance/developer.md"),
    )
    return root


def main(argv=None) -> None:
    args = parser().parse_args(argv)
    settings = MaintenanceSettings()
    if args.root:
        settings.root = args.root
    if args.command == "agent":
        from .agent import SourceRepairAgent

        meta = args.worktree / ".maintenance"
        round_ = json.loads((meta / "round.json").read_text())
        if round_["round_id"] != args.round_id:
            raise SystemExit("round identity mismatch")
        slot = meta / args.round_id
        slot.mkdir(parents=True, exist_ok=True)
        feedback = (
            json.loads((meta / "feedback.json").read_text())
            if (meta / "feedback.json").is_file()
            else None
        )
        worker = SourceRepairAgent(
            home=Path("/codex-home"), prompt=args.prompt, model=args.model, effort=args.effort
        )
        receipt = asyncio.run(
            worker.run(
                args.worktree,
                meta / "context.json",
                slot / "receipt.json",
                slot / "report.json",
                thread_id=args.thread_id,
                feedback=feedback,
            )
        )
        from .evidence import write_json

        write_json(meta / "receipt.json", receipt)
        return
    if args.command in {"guardian", "evidence-server"} and not settings.enabled:
        print(json.dumps({"enabled": False, "message": "source maintenance disabled"}))
        return
    if args.command in {"status", "inspect", "issues"} and not settings.database.exists():
        print(json.dumps({"enabled": settings.enabled, "incidents": []}))
        return
    from .repository import MaintenanceRepository

    repository = MaintenanceRepository(
        settings.database, readonly=args.command == "evidence-server"
    )
    if args.command == "status":
        print(json.dumps([i.model_dump(mode="json") for i in repository.incidents()], indent=2))
    elif args.command == "inspect":
        print(repository.get(args.incident_id).model_dump_json(indent=2))
    elif args.command in {"retry", "cancel"}:
        repository.operator(args.incident_id, retry=args.command == "retry", reason=args.reason)
    elif args.command == "adopt":
        repository.observe(Incident.model_validate_json(args.file.read_text()))
    elif args.command == "replay":
        for value in json.loads(args.file.read_text()):
            repository.record_sample(HealthSample.model_validate(value))
    elif args.command == "issues":
        from .issues import render

        print(render(repository, settings.root / "message-source-issues.md"))
    elif args.command == "evidence-server":
        import uvicorn

        from .evidence import EvidenceStore, evidence_app

        if not settings.evidence_token_file:
            raise SystemExit("evidence token required")
        uvicorn.run(
            evidence_app(
                EvidenceStore(settings.root, repository),
                settings.evidence_token_file.read_text().strip(),
            ),
            host="0.0.0.0",
            port=settings.evidence_port,
        )
    else:
        from .controller import Controller

        controller = Controller(settings, repository)
        while True:
            controller.tick()
            if args.once:
                break
            time.sleep(settings.scan_seconds)


if __name__ == "__main__":
    main()
