"""Command line entrypoint for the Monitoring Message Bus."""

from __future__ import annotations

import argparse
import json
import sys
import time
from typing import Any

from doxagent.monitoring.schema import MonitoringParameters, UpdateActor
from doxagent.monitoring.service import MonitoringBusService, snapshot_to_agent_payload
from doxagent.stocktwits.schema import BootstrapEventPolicy, TickerMode


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    service = MonitoringBusService.from_settings()

    if args.command == "init":
        service.repository.ensure_defaults()
        _print_json(
            {
                "ok": True,
                "sources": [s.model_dump(mode="json") for s in service.list_sources()],
            }
        )
        return 0
    if args.command == "sources":
        _print_json({"sources": [s.model_dump(mode="json") for s in service.list_sources()]})
        return 0
    if args.command == "status":
        snapshot = service.status_snapshot(ticker=args.ticker, limit=args.limit)
        _print_json(snapshot_to_agent_payload(snapshot))
        return 0
    if args.command == "ticker-config":
        _print_json(service.get_ticker_config(args.ticker))
        return 0
    if args.command == "bind":
        binding = service.configure_ticker_source(
            args.ticker,
            args.source,
            parameters=MonitoringParameters(
                keywords=args.keyword,
                usernames=args.username,
                search_terms=args.search_term,
                rss_urls=args.rss_url,
                source_filters=args.source_filter,
            ),
            enabled=not args.disabled,
            updated_by=UpdateActor.USER,
            updated_reason=args.reason,
            merge=not args.replace,
        )
        _print_json({"binding": binding.model_dump(mode="json")})
        return 0
    if args.command == "unbind":
        removed = service.delete_ticker_source(args.ticker, args.source)
        _print_json(
            {
                "ok": True,
                "removed": removed,
                "ticker": args.ticker.strip().upper(),
                "source_id": args.source.strip().lower(),
            }
        )
        return 0
    if args.command == "delete-ticker":
        deleted_count = service.delete_ticker_config(args.ticker)
        _print_json(
            {
                "ok": True,
                "deleted_count": deleted_count,
                "ticker": args.ticker.strip().upper(),
            }
        )
        return 0
    if args.command == "set-poll-interval":
        source = service.set_source_poll_interval(
            args.source,
            seconds=args.seconds,
            updated_by=UpdateActor.USER,
        )
        _print_json({"source": source.model_dump(mode="json")})
        return 0
    if args.command == "set-stocktwits-config":
        output = service.configure_stocktwits_persistence(
            args.ticker,
            enabled=_optional_bool(args.enabled, args.disabled),
            mode=TickerMode(args.mode) if args.mode else None,
            target_cadence_seconds=args.target_cadence_seconds,
            hot_cadence_seconds=args.hot_cadence_seconds,
            page_size=args.page_size,
            max_pages_per_crawl=args.max_pages_per_crawl,
            hot_message_threshold=args.hot_message_threshold,
            hot_cooldown_successes=args.hot_cooldown_successes,
            bootstrap_event_policy=(
                BootstrapEventPolicy(args.bootstrap_event_policy)
                if args.bootstrap_event_policy
                else None
            ),
            reset_schedule=args.reset_schedule,
            updated_reason=args.reason,
        )
        _print_json(output)
        return 0
    if args.command == "poll-due":
        results = service.poll_due_once()
        _print_json({"results": [result.model_dump(mode="json") for result in results]})
        return 0
    if args.command == "enrich-media":
        _print_json(
            service.enrich_recent_media(
                ticker=args.ticker,
                limit=args.limit,
                concurrency=args.concurrency,
                dry_run=args.dry_run,
                incomplete_only=not args.include_complete,
            )
        )
        return 0
    if args.command == "poll-forever":
        print(
            "warning: monitoring poll-forever is a debug/lower-level entry point. "
            "Formal ticker runtime should use python -m doxagent.runtime_scheduler.cli run-loop "
            "so scheduler trading-session rules remain authoritative.",
            file=sys.stderr,
            flush=True,
        )
        _poll_forever(
            service,
            sleep_seconds=args.sleep_seconds,
            immediate=not args.no_immediate,
        )
        return 0
    parser.error(f"Unsupported command: {args.command}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m doxagent.monitoring.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="Initialize the durable monitoring store and default sources.")
    sub.add_parser("sources", help="List configured monitoring sources.")

    status = sub.add_parser("status", help="Show source, poll, message, and event-stream status.")
    status.add_argument("--ticker")
    status.add_argument("--limit", type=int, default=20)

    ticker_config = sub.add_parser("ticker-config", help="Show one ticker's monitoring config.")
    ticker_config.add_argument("ticker")

    bind = sub.add_parser("bind", help="Create or update a ticker/source binding.")
    bind.add_argument("ticker")
    bind.add_argument("--source", required=True)
    bind.add_argument("--keyword", action="append", default=[])
    bind.add_argument("--username", action="append", default=[])
    bind.add_argument("--search-term", action="append", default=[])
    bind.add_argument("--rss-url", action="append", default=[])
    bind.add_argument("--source-filter", action="append", default=[])
    bind.add_argument("--disabled", action="store_true")
    bind.add_argument("--replace", action="store_true")
    bind.add_argument("--reason")

    unbind = sub.add_parser("unbind", help="Delete one ticker/source monitoring binding.")
    unbind.add_argument("ticker")
    unbind.add_argument("--source", required=True)

    delete_ticker = sub.add_parser(
        "delete-ticker",
        help="Delete all monitoring bindings for a ticker.",
    )
    delete_ticker.add_argument("ticker")

    interval = sub.add_parser("set-poll-interval", help="User-only polling cadence update.")
    interval.add_argument("source")
    interval.add_argument("seconds", type=int)

    stocktwits = sub.add_parser(
        "set-stocktwits-config",
        help="User-only Stocktwits durable polling configuration for one ticker.",
    )
    stocktwits.add_argument("ticker")
    stocktwits.add_argument("--enabled", action="store_true")
    stocktwits.add_argument("--disabled", action="store_true")
    stocktwits.add_argument("--mode", choices=[item.value for item in TickerMode])
    stocktwits.add_argument("--target-cadence-seconds", type=int)
    stocktwits.add_argument("--hot-cadence-seconds", type=int)
    stocktwits.add_argument("--page-size", type=int)
    stocktwits.add_argument("--max-pages-per-crawl", type=int)
    stocktwits.add_argument("--hot-message-threshold", type=int)
    stocktwits.add_argument("--hot-cooldown-successes", type=int)
    stocktwits.add_argument(
        "--bootstrap-event-policy",
        choices=[item.value for item in BootstrapEventPolicy],
    )
    stocktwits.add_argument("--reset-schedule", action="store_true")
    stocktwits.add_argument("--reason")

    sub.add_parser("poll-due", help="Poll every due enabled binding once.")

    enrich = sub.add_parser(
        "enrich-media",
        help="Fetch and persist supplemental full article bodies for media messages.",
    )
    enrich.add_argument("--ticker")
    enrich.add_argument("--limit", type=int, default=50)
    enrich.add_argument("--concurrency", type=int, default=6)
    enrich.add_argument("--dry-run", action="store_true")
    enrich.add_argument(
        "--include-complete",
        action="store_true",
        help="Attempt already complete-like media rows too.",
    )

    poll_forever = sub.add_parser(
        "poll-forever",
        help=(
            "Debug/lower-level continuous Message Bus poller. Formal runtime should use "
            "python -m doxagent.runtime_scheduler.cli run-loop."
        ),
    )
    poll_forever.add_argument("--sleep-seconds", type=int, default=15)
    poll_forever.add_argument("--no-immediate", action="store_true")
    return parser


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _optional_bool(enabled: bool, disabled: bool) -> bool | None:
    if enabled and disabled:
        raise ValueError("--enabled and --disabled are mutually exclusive.")
    if enabled:
        return True
    if disabled:
        return False
    return None


def _poll_forever(
    service: MonitoringBusService,
    *,
    sleep_seconds: int,
    immediate: bool,
) -> None:
    sleep_interval = max(1, sleep_seconds)
    if immediate:
        _print_poll_cycle(service)
    while True:
        time.sleep(sleep_interval)
        _print_poll_cycle(service)


def _print_poll_cycle(service: MonitoringBusService) -> None:
    results = service.poll_due_once()
    if not results:
        return
    payload = {
        "ok": True,
        "command": "poll-forever",
        "results": [result.model_dump(mode="json") for result in results],
    }
    print(json.dumps(payload, ensure_ascii=False, default=str), flush=True)


if __name__ == "__main__":  # pragma: no cover - exercised manually.
    raise SystemExit(main())
