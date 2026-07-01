"""Command line entrypoint for the standalone Stocktwits polling crawler."""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

from doxagent.settings import DoxAgentSettings
from doxagent.stocktwits.client import StocktwitsHTTPClient
from doxagent.stocktwits.crawler import StocktwitsPollingCrawler, config_from_settings
from doxagent.stocktwits.repository import (
    migrate_postgres_stocktwits_to_sqlite,
    repository_from_settings,
)
from doxagent.stocktwits.schema import normalize_symbols, parse_symbol_csv


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    settings = DoxAgentSettings()

    if args.command == "migrate-from-postgres":
        stats = migrate_postgres_stocktwits_to_sqlite(
            source_database_url=args.source_database_url or settings.require_database_url(),
            sqlite_path=args.sqlite_path or settings.stocktwits_sqlite_path,
            batch_size=args.batch_size,
        )
        _print_json({"ok": True, "migration": stats})
        return 0

    try:
        crawler = _crawler(settings, storage_mode=args.storage)
    except RuntimeError as exc:
        parser.error(str(exc))

    if args.command == "init":
        crawler.repository.ensure_schema()
        symbols = _symbols_arg(args.symbols, settings.stocktwits_default_symbols)
        states = crawler.initialize_tickers(symbols, reset_schedule=args.reset_schedule)
        _print_json(
            {"ok": True, "ticker_states": [state.model_dump(mode="json") for state in states]}
        )
        return 0

    if args.command == "status":
        snapshot = crawler.status_snapshot(symbol=args.symbol, limit=args.limit)
        _print_json(snapshot.model_dump(mode="json"))
        return 0

    if args.command == "run-once":
        crawler.repository.ensure_schema()
        if args.symbol:
            runs = [crawler.crawl_symbol(args.symbol)]
        else:
            runs = crawler.poll_due_once(max_tickers=args.max_tickers)
        _print_json({"runs": [run.model_dump(mode="json") for run in runs]})
        return 0

    if args.command == "run-loop":
        crawler.repository.ensure_schema()
        _run_loop(
            crawler,
            tick_seconds=args.tick_seconds or settings.stocktwits_scheduler_tick_seconds,
            max_tickers=args.max_tickers,
            max_cycles=args.max_cycles,
        )
        return 0

    parser.error(f"Unsupported command: {args.command}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m doxagent.stocktwits.cli")
    parser.add_argument(
        "--storage",
        choices=["sqlite", "memory"],
        help="Override DOXAGENT_STOCKTWITS_STORAGE_MODE.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="Create schema and configure staggered ticker states.")
    init.add_argument("--symbols", help="Comma-separated ticker list. Defaults to 10 MVP tickers.")
    init.add_argument("--reset-schedule", action="store_true")

    status = sub.add_parser("status", help="Show ticker states and recent crawl runs.")
    status.add_argument("--symbol")
    status.add_argument("--limit", type=int, default=20)

    run_once = sub.add_parser("run-once", help="Poll one due ticker or one explicit symbol.")
    run_once.add_argument("--symbol")
    run_once.add_argument("--max-tickers", type=int, default=1)

    run_loop = sub.add_parser(
        "run-loop",
        help="Continuously poll due tickers with staggered cadence.",
    )
    run_loop.add_argument("--tick-seconds", type=int)
    run_loop.add_argument("--max-tickers", type=int, default=1)
    run_loop.add_argument(
        "--max-cycles",
        type=int,
        help="Optional finite loop count for server smoke tests.",
    )

    migrate = sub.add_parser(
        "migrate-from-postgres",
        help="One-off copy from old Supabase/Postgres Stocktwits tables into local SQLite.",
    )
    migrate.add_argument(
        "--source-database-url",
        help="Old Supabase/Postgres URL. Defaults to DOXAGENT_DATABASE_URL.",
    )
    migrate.add_argument(
        "--sqlite-path",
        help="Target local SQLite path. Defaults to DOXAGENT_STOCKTWITS_SQLITE_PATH.",
    )
    migrate.add_argument("--batch-size", type=int, default=1000)
    return parser


def _crawler(settings: DoxAgentSettings, *, storage_mode: str | None) -> StocktwitsPollingCrawler:
    return StocktwitsPollingCrawler(
        repository=repository_from_settings(settings, storage_mode=storage_mode),
        client=StocktwitsHTTPClient(settings),
        config=config_from_settings(settings),
    )


def _symbols_arg(value: str | None, default_value: str) -> list[str]:
    return normalize_symbols(value.split(",")) if value else parse_symbol_csv(default_value)


def _run_loop(
    crawler: StocktwitsPollingCrawler,
    *,
    tick_seconds: int,
    max_tickers: int,
    max_cycles: int | None,
) -> None:
    cycles = 0
    while True:
        runs = crawler.poll_due_once(max_tickers=max_tickers)
        if runs:
            _print_json(
                {
                    "ok": True,
                    "command": "run-loop",
                    "runs": [run.model_dump(mode="json") for run in runs],
                }
            )
        cycles += 1
        if max_cycles is not None and cycles >= max_cycles:
            return
        time.sleep(max(1, tick_seconds))


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str), flush=True)


if __name__ == "__main__":  # pragma: no cover - manual entrypoint.
    raise SystemExit(main())
