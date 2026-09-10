"""Ticker initialization V2 CLI with production adapters."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
from datetime import datetime
from pathlib import Path

from .repository import InitializationRepository
from .schema import InitializationError, NodeSpec, RunStatus
from .service import InitializationWorker


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="doxagent-ticker-init")
    root.add_argument("--database", type=Path, default=None)
    commands = root.add_subparsers(dest="command", required=True)
    for name in ("submit", "reinitialize"):
        command = commands.add_parser(name)
        command.add_argument("--ticker", required=True)
        command.add_argument("--research-cutoff-at", required=True)
        command.add_argument(
            "--plan",
            type=Path,
            help="Optional explicit node plan; defaults to the production V2 topology",
        )
    for name in ("status", "inspect-node", "resume", "rerun-node", "rerun-block"):
        command = commands.add_parser(name)
        command.add_argument("--initialization-id", required=True)
        if name in {"inspect-node", "rerun-node", "resume"}:
            command.add_argument("--node", required=name != "resume")
        if name == "rerun-block":
            command.add_argument("--block", required=True)
        if name in {"resume", "rerun-node", "rerun-block"}:
            command.add_argument("--reason", required=True)
    worker = commands.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    activate = commands.add_parser("activate")
    activate.add_argument("--ticker", required=True)
    activate.add_argument("--artifacts", type=Path, required=True)
    activate.add_argument("--research-cutoff-at", required=True)
    activate.add_argument("--reason", required=True)
    replace = commands.add_parser("replace-artifact")
    replace.add_argument("--ticker", required=True)
    replace.add_argument("--role", required=True)
    replace.add_argument("--reference", type=Path, required=True)
    replace.add_argument("--reason", required=True)
    rollback = commands.add_parser("rollback")
    rollback.add_argument("--ticker", required=True)
    rollback.add_argument("--revision", required=True)
    rollback.add_argument("--reason", required=True)
    for name in ("adopt-artifact", "invalidate"):
        command = commands.add_parser(name)
        command.add_argument("--initialization-id", required=True)
        command.add_argument(
            "--node", required=True, action="append" if name == "invalidate" else "store"
        )
        command.add_argument("--reason", required=True)
        if name == "adopt-artifact":
            command.add_argument("--result", type=Path, required=True)
    return root


async def _worker(repo: InitializationRepository, once: bool) -> int:
    from doxagent.trade_execution.worker import WriterLock

    from .catalog import adapter_factory

    owner = WriterLock(repo.path.parent / "initialization-worker")
    owner.__enter__()

    worker = InitializationWorker(repo, adapter_factory)
    from doxagent.settings import DoxAgentSettings

    from .sync import SupabaseSummarySink, flush_summaries

    settings = DoxAgentSettings()
    sink = (
        SupabaseSummarySink(
            settings.ticker_initialization_summary_url, settings.ticker_initialization_summary_key
        )
        if settings.ticker_initialization_summary_url and settings.ticker_initialization_summary_key
        else None
    )

    async def mirror() -> None:
        assert sink is not None
        while True:
            await flush_summaries(repo, sink)
            await asyncio.sleep(60)

    mirror_task = asyncio.create_task(mirror()) if sink is not None else None
    current = asyncio.current_task()
    previous_signal = signal.getsignal(signal.SIGTERM)
    loop = asyncio.get_running_loop()

    def terminate(_number: int, _frame: object) -> None:
        if current is not None:
            loop.call_soon_threadsafe(current.cancel)

    signal.signal(signal.SIGTERM, terminate)
    try:
        return await _run_loop(worker, once)
    except asyncio.CancelledError:
        return 130
    finally:
        owner.__exit__()
        signal.signal(signal.SIGTERM, previous_signal)
        if mirror_task is not None:
            mirror_task.cancel()
            await asyncio.gather(mirror_task, return_exceptions=True)
        if sink is not None:
            await sink.close()


async def _run_loop(worker: InitializationWorker, once: bool) -> int:
    while True:
        result = await worker.run_once()
        if result is not None:
            print(result.model_dump_json())
        if once:
            return 1 if result and result.status == RunStatus.FAILED else 0
        if result is None:
            await asyncio.sleep(1)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        from doxagent.settings import DoxAgentSettings

        repo = InitializationRepository(
            args.database
            or DoxAgentSettings().ticker_initialization_control_path
            or Path(".tmp/ticker_initialization/control.sqlite3")
        )
        if args.command in {"submit", "reinitialize"}:
            from .catalog import default_plan

            plan = (
                [
                    NodeSpec.model_validate(n)
                    for n in json.loads(args.plan.read_text(encoding="utf-8"))
                ]
                if args.plan
                else default_plan()
            )
            run = repo.submit(
                args.ticker,
                datetime.fromisoformat(args.research_cutoff_at),
                plan,
                reinitialize=args.command == "reinitialize",
            )
            print(run.model_dump_json(indent=2))
        elif args.command == "resume":
            print(
                repo.resume(
                    args.initialization_id, reason=args.reason, node_key=args.node
                ).model_dump_json(indent=2)
            )
        elif args.command in {"rerun-node", "rerun-block"}:
            print(
                repo.rerun(
                    args.initialization_id,
                    reason=args.reason,
                    node_key=getattr(args, "node", None),
                    block=getattr(args, "block", None),
                ).model_dump_json(indent=2)
            )
        elif args.command == "status":
            run = repo.get(args.initialization_id)
            nodes = repo.nodes(run.initialization_id)
            active = repo.active_revision(run.ticker)
            print(
                json.dumps(
                    {
                        **run.model_dump(mode="json"),
                        "current_nodes": [n.key for n in nodes if n.status == "RUNNING"],
                        "failed_nodes": [n.key for n in nodes if n.status == "FAILED"],
                        "attempt_count": sum(
                            len(repo.attempts(run.initialization_id, n.key)) for n in nodes
                        ),
                        "active_revision_id": active["revision_id"] if active else None,
                        "cloud_sync_pending": any(
                            s["initialization_id"] == run.initialization_id for s in repo.outbox()
                        ),
                        "nodes": [
                            n.model_dump(mode="json", exclude={"inputs", "receipt", "result"})
                            for n in nodes
                        ],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "inspect-node":
            nodes = repo.nodes(args.initialization_id)
            node = next((n for n in nodes if n.key == args.node), None)
            if node is None:
                raise KeyError(args.node)
            print(node.model_dump_json(indent=2))
        elif args.command == "adopt-artifact":
            from .schema import NodeResult

            print(
                repo.adopt(
                    args.initialization_id,
                    args.node,
                    NodeResult.model_validate_json(args.result.read_text(encoding="utf-8")),
                    reason=args.reason,
                ).model_dump_json(indent=2)
            )
        elif args.command == "invalidate":
            print(
                repo.invalidate(
                    args.initialization_id, args.node, reason=args.reason
                ).model_dump_json(indent=2)
            )
        elif args.command == "activate":
            from .operations import submit_activation

            print(
                submit_activation(
                    repo,
                    args.ticker,
                    artifacts=json.loads(args.artifacts.read_text(encoding="utf-8")),
                    reason=args.reason,
                    cutoff=datetime.fromisoformat(args.research_cutoff_at),
                ).model_dump_json(indent=2)
            )
        elif args.command == "rollback":
            from .operations import submit_activation

            revision = repo.revision(args.revision)
            if revision["ticker"] != args.ticker.upper():
                raise ValueError("rollback revision ticker mismatch")
            print(
                submit_activation(
                    repo,
                    args.ticker,
                    artifacts=revision["artifacts"],
                    reason=args.reason,
                    cutoff=datetime.fromisoformat(revision["research_cutoff_at"]),
                    operation="ROLLBACK",
                ).model_dump_json(indent=2)
            )
        elif args.command == "replace-artifact":
            from .operations import replace_artifact

            print(
                replace_artifact(
                    repo,
                    args.ticker,
                    role=args.role,
                    reference=json.loads(args.reference.read_text(encoding="utf-8")),
                    reason=args.reason,
                ).model_dump_json(indent=2)
            )
        else:
            return asyncio.run(_worker(repo, args.once))
        return 0
    except (InitializationError, ValueError, KeyError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
