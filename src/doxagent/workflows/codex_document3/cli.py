"""CLI entry point for explicit D3 initialize and maintenance runs."""

from __future__ import annotations

import argparse
import asyncio

from doxagent.settings import DoxAgentSettings

from .service import build_document3_orchestrator


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="codex-document3")
    subparsers = parser.add_subparsers(dest="command", required=True)
    initialize = subparsers.add_parser("initialize")
    initialize.add_argument("--ticker", required=True)
    initialize.add_argument("--document2-run-id", required=True)
    initialize.add_argument("--event-library-version", type=int)
    initialize.add_argument("--run-id")
    maintain = subparsers.add_parser("maintain")
    maintain.add_argument("--ticker", required=True)
    maintain.add_argument("--event-library-version", type=int)
    maintain.add_argument("--run-id")
    return parser


async def _run(args: argparse.Namespace) -> int:
    orchestrator = build_document3_orchestrator(DoxAgentSettings())
    if args.command == "initialize":
        result = await orchestrator.initialize(
            ticker=args.ticker,
            document2_run_id=args.document2_run_id,
            event_library_version=args.event_library_version,
            run_id=args.run_id,
        )
    else:
        result = await orchestrator.maintain(
            ticker=args.ticker,
            event_library_version=args.event_library_version,
            run_id=args.run_id,
        )
    print(result.model_dump_json(indent=2))
    return 0


def main() -> int:
    return asyncio.run(_run(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
