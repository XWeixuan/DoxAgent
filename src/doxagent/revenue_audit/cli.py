"""Independent low-frequency runner for paper-trading revenue audits."""

from __future__ import annotations

import argparse
import json
import signal
from datetime import date, datetime
from threading import Event

from doxagent.revenue_audit.schema import RevenueAuditRun
from doxagent.revenue_audit.service import RevenueAuditService
from doxagent.runtime_scheduler import DashboardStateAPI
from doxagent.runtime_scheduler.schema import AuditSeverity, RuntimeAuditEvent
from doxagent.settings import DoxAgentSettings


def main(argv: list[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    settings = DoxAgentSettings()
    service = RevenueAuditService.from_settings(settings)
    dashboard = DashboardStateAPI.from_settings()
    if args.command == "audit-date":
        run = service.audit_date(args.ticker, date.fromisoformat(args.date))
        _record_run_event(dashboard, run)
        _print(run)
        return 0 if run.status.value != "failed" else 1
    if args.command == "run-due":
        runs = service.audit_due(now=_parse_datetime(args.now))
        for run in runs:
            _record_run_event(dashboard, run)
        _print({"runs": [run.model_dump(mode="json") for run in runs]})
        return 0 if all(run.status.value != "failed" for run in runs) else 1
    if args.command == "run-loop":
        stop_event = Event()
        _install_stop_handlers(stop_event)
        failures = 0
        iterations = 0
        while not stop_event.is_set():
            iterations += 1
            try:
                runs = service.audit_due(now=_parse_datetime(args.now))
                for run in runs:
                    _record_run_event(dashboard, run)
                if not args.quiet and runs:
                    _print(
                        {
                            "iteration": iterations,
                            "runs": [run.model_dump(mode="json") for run in runs],
                        }
                    )
            except Exception as exc:
                failures += 1
                _print(
                    {
                        "iteration": iterations,
                        "status": "failed",
                        "error_type": type(exc).__name__,
                        "error": str(exc)[:500],
                    }
                )
            if args.max_iterations is not None and iterations >= args.max_iterations:
                break
            if stop_event.wait(args.sleep_seconds):
                break
        _print({"iterations": iterations, "failure_count": failures})
        return 0 if failures == 0 else 1
    parser.error(f"Unsupported command: {args.command}")


def _parser() -> argparse.ArgumentParser:
    settings = DoxAgentSettings()
    parser = argparse.ArgumentParser(prog="python -m doxagent.revenue_audit.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    audit_date = sub.add_parser("audit-date")
    audit_date.add_argument("ticker")
    audit_date.add_argument("date")
    run_due = sub.add_parser("run-due")
    run_due.add_argument("--now")
    run_loop = sub.add_parser("run-loop")
    run_loop.add_argument(
        "--sleep-seconds",
        type=float,
        default=float(settings.revenue_audit_loop_sleep_seconds),
    )
    run_loop.add_argument("--max-iterations", type=int)
    run_loop.add_argument("--now")
    run_loop.add_argument("--quiet", action="store_true")
    return parser


def _record_run_event(dashboard: DashboardStateAPI, run: RevenueAuditRun) -> None:
    dashboard.scheduler.repository.append_audit_event(
        RuntimeAuditEvent(
            ticker=run.ticker,
            event_type="audit.revenue.status_changed",
            severity=(AuditSeverity.ERROR if run.status.value == "failed" else AuditSeverity.INFO),
            message=f"Revenue audit {run.status.value} for {run.audit_date.isoformat()}.",
            payload={
                "audit_run_id": run.run_id,
                "ticker": run.ticker,
                "date": run.audit_date.isoformat(),
                "status": run.status.value,
                "record_count": run.record_count,
                "audited_count": run.audited_count,
                "issue_count": run.issue_count,
                "method_version": run.method_version,
                "config_fingerprint": run.config_fingerprint,
            },
        )
    )


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _install_stop_handlers(stop_event: Event) -> None:
    def stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(signal_name, stop)
        except (ValueError, OSError):
            continue


def _print(value: RevenueAuditRun | dict[str, object]) -> None:
    payload = value.model_dump(mode="json") if isinstance(value, RevenueAuditRun) else value
    print(json.dumps(payload, ensure_ascii=False, default=str))


if __name__ == "__main__":
    raise SystemExit(main())
