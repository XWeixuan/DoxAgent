"""Operational CLI for enqueueing and advancing O4 without a real-model test harness."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from doxagent.settings import DoxAgentSettings

from .service import build_monitoring_o4_runtime


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="doxagent-monitoring-o4")
    commands = parser.add_subparsers(dest="command", required=True)
    configure = commands.add_parser("configure")
    configure.add_argument("--ticker", required=True)
    configure.add_argument("--policy-set", required=True)
    configure.add_argument("--document2", required=True)
    commands.add_parser("scan-alerts")
    commands.add_parser("run-once")
    status = commands.add_parser("status")
    status.add_argument("--ticker")
    return parser


async def _main(args: argparse.Namespace) -> int:
    runtime = build_monitoring_o4_runtime(DoxAgentSettings())
    try:
        if args.command == "configure":
            request = runtime.orchestrator.submit_configure(
                ticker=args.ticker,
                policy_set=json.loads(Path(args.policy_set).read_text(encoding="utf-8")),
                document2=json.loads(Path(args.document2).read_text(encoding="utf-8")),
                reason="operator_cli",
            )
            print(request.model_dump_json(indent=2))
        elif args.command == "scan-alerts":
            if runtime.dispatcher is None:
                raise RuntimeError("Message Bus v2 is disabled; alert scan is unavailable")
            print(
                json.dumps(
                    [item.model_dump(mode="json") for item in runtime.dispatcher.scan()],
                    indent=2,
                )
            )
        elif args.command == "run-once":
            result = await runtime.orchestrator.process_next()
            print(result.model_dump_json(indent=2) if result else '{"status":"idle"}')
        else:
            print(
                json.dumps(
                    [
                        item.model_dump(mode="json")
                        for item in runtime.repository.list_requests(ticker=args.ticker)
                    ],
                    ensure_ascii=False,
                    indent=2,
                    default=str,
                )
            )
        return 0
    finally:
        await runtime.close()


def main() -> None:
    raise SystemExit(asyncio.run(_main(build_parser().parse_args())))


if __name__ == "__main__":
    main()
