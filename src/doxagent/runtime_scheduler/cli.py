"""CLI for the unified runtime scheduler control plane."""

from __future__ import annotations

import argparse
import json
import signal
from datetime import datetime
from threading import Event
from typing import Any

from doxagent.runtime_scheduler.api import DashboardStateAPI
from doxagent.runtime_scheduler.loop import RuntimeLoopCycle, RuntimeSchedulerLoop
from doxagent.runtime_scheduler.schema import RefreshRequestSource
from doxagent.settings import DoxAgentSettings


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    api = DashboardStateAPI.from_settings()
    now = _parse_datetime(args.now) if hasattr(args, "now") else None

    if args.command == "overview":
        _print_json(api.list_tickers().model_dump(mode="json"))
        return 0
    if args.command == "detail":
        _print_json(api.get_ticker(args.ticker).model_dump(mode="json"))
        return 0
    if args.command == "start":
        detail = api.scheduler.start_ticker(
            args.ticker,
            now=now,
            force_initialize=args.force_initialize,
        )
        _print_json(detail.model_dump(mode="json"))
        return 0
    if args.command == "pause":
        _print_json(api.pause_ticker(args.ticker, reason=args.reason).model_dump(mode="json"))
        return 0
    if args.command == "stop":
        detail = api.stop_ticker(
            args.ticker,
            reason=args.reason,
            disable_bindings=not args.keep_bindings,
        )
        _print_json(detail.model_dump(mode="json"))
        return 0
    if args.command == "tick":
        detail = api.tick(args.ticker, now=now, event_limit=args.event_limit)
        _print_json(detail.model_dump(mode="json"))
        return 0
    if args.command == "tick-all":
        details = api.scheduler.run_due_once(now=now, event_limit=args.event_limit)
        _print_json({"tickers": [detail.model_dump(mode="json") for detail in details]})
        return 0
    if args.command == "run-loop":
        stop_event = Event()
        _install_stop_handlers(stop_event)
        loop = RuntimeSchedulerLoop(
            api.scheduler,
            sleep_seconds=args.sleep_seconds,
            event_limit=args.event_limit,
            stop_event=stop_event,
        )
        summary = loop.run(
            immediate=not args.no_immediate,
            max_iterations=args.max_iterations,
            now_fn=(lambda: now) if now is not None else None,
            on_cycle=None if args.quiet else _print_loop_cycle,
        )
        _print_json(
            {
                "ok": summary.failure_count == 0,
                "command": "run-loop",
                "summary": {
                    "started_at": summary.started_at,
                    "stopped_at": summary.stopped_at,
                    "iteration_count": summary.iteration_count,
                    "failure_count": summary.failure_count,
                },
            }
        )
        return 0 if summary.failure_count == 0 else 1
    if args.command == "docs":
        _print_json(api.document_status(args.ticker).model_dump(mode="json"))
        return 0
    if args.command in {"monitoring", "message-bus"}:
        _print_json(api.monitoring_status(args.ticker).model_dump(mode="json"))
        return 0
    if args.command in {"events", "runtime"}:
        _print_json(api.event_processing_status(args.ticker).model_dump(mode="json"))
        return 0
    if args.command == "trade-intents":
        _print_json(
            {
                "trade_intents": [
                    item.model_dump(mode="json")
                    for item in api.scheduler.trade_intents(args.ticker, limit=args.limit)
                ]
            }
        )
        return 0
    if args.command == "audit":
        events = api.scheduler.repository.list_audit_events(
            ticker=args.ticker,
            limit=args.limit,
        )
        _print_json({"audit_events": [event.model_dump(mode="json") for event in events]})
        return 0
    if args.command == "request-refresh":
        request = api.request_document_refresh(
            args.ticker,
            requested_by=RefreshRequestSource(args.requested_by),
            reason=args.reason,
            trigger_event_id=args.trigger_event_id,
        )
        _print_json(request.model_dump(mode="json"))
        return 0
    parser.error(f"Unsupported command: {args.command}")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m doxagent.runtime_scheduler.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    settings = DoxAgentSettings()

    sub.add_parser("overview", help="List ticker runtime overview states.")

    detail = sub.add_parser("detail", help="Show one ticker runtime detail.")
    detail.add_argument("ticker")

    start = sub.add_parser("start", help="Start one ticker runtime.")
    start.add_argument("ticker")
    start.add_argument("--force-initialize", action="store_true")
    start.add_argument("--now", help="Optional ISO datetime for deterministic local checks.")

    pause = sub.add_parser("pause", help="Pause one ticker runtime.")
    pause.add_argument("ticker")
    pause.add_argument("--reason")

    stop = sub.add_parser("stop", help="Stop one ticker runtime.")
    stop.add_argument("ticker")
    stop.add_argument("--reason")
    stop.add_argument("--keep-bindings", action="store_true")

    tick = sub.add_parser("tick", help="Run one scheduler tick for a ticker.")
    tick.add_argument("ticker")
    tick.add_argument("--event-limit", type=int, default=100)
    tick.add_argument("--now", help="Optional ISO datetime for deterministic local checks.")

    tick_all = sub.add_parser("tick-all", help="Run one scheduler tick for all active tickers.")
    tick_all.add_argument("--event-limit", type=int, default=100)
    tick_all.add_argument("--now", help="Optional ISO datetime for deterministic local checks.")

    run_loop = sub.add_parser(
        "run-loop",
        help="Formal long-running runtime loop; calls scheduler tick for active tickers.",
    )
    run_loop.add_argument("--event-limit", type=int, default=100)
    run_loop.add_argument(
        "--sleep-seconds",
        type=float,
        default=float(settings.runtime_scheduler_loop_sleep_seconds),
    )
    run_loop.add_argument("--max-iterations", type=int)
    run_loop.add_argument("--no-immediate", action="store_true")
    run_loop.add_argument("--quiet", action="store_true")
    run_loop.add_argument("--now", help="Optional fixed ISO datetime for deterministic checks.")

    docs = sub.add_parser("docs", help="Show Document 1/2/3 availability.")
    docs.add_argument("ticker")

    monitoring = sub.add_parser("monitoring", help="Show Message Bus status for one ticker.")
    monitoring.add_argument("ticker")

    message_bus = sub.add_parser(
        "message-bus",
        help="Show Message Bus status for one ticker; alias aligned to Dashboard contract.",
    )
    message_bus.add_argument("ticker")

    events = sub.add_parser("events", help="Show runtime event-processing status.")
    events.add_argument("ticker")

    runtime = sub.add_parser(
        "runtime",
        help="Show runtime event-processing status; alias aligned to Dashboard contract.",
    )
    runtime.add_argument("ticker")

    intents = sub.add_parser("trade-intents", help="Show recent trade intents.")
    intents.add_argument("ticker")
    intents.add_argument("--limit", type=int, default=50)

    audit = sub.add_parser("audit", help="Show scheduler audit events.")
    audit.add_argument("--ticker")
    audit.add_argument("--limit", type=int, default=100)

    refresh = sub.add_parser("request-refresh", help="Submit a document refresh request.")
    refresh.add_argument("ticker")
    refresh.add_argument(
        "--requested-by",
        choices=[item.value for item in RefreshRequestSource],
        default=RefreshRequestSource.USER.value,
    )
    refresh.add_argument("--reason", required=True)
    refresh.add_argument("--trigger-event-id")
    return parser


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))


def _print_loop_cycle(cycle: RuntimeLoopCycle) -> None:
    _print_json(
        {
            "ok": cycle.error is None,
            "command": "run-loop",
            "cycle": {
                "iteration": cycle.iteration,
                "generated_at": cycle.generated_at,
                "ticker_count": cycle.ticker_count,
                "tickers": cycle.tickers,
                "error": cycle.error,
            },
        }
    )


def _install_stop_handlers(stop_event: Event) -> None:
    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, request_stop)


if __name__ == "__main__":  # pragma: no cover - exercised manually.
    raise SystemExit(main())
