"""Local deterministic executor operations. All commands default to the Runtime DB."""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.settings import DoxAgentSettings

from .acceptance import create_suite
from .executor import Executor
from .ibkr_session import IbkrSession
from .repository import ExecutionRepository
from .schema import ExecutionProfile
from .worker import WriterLock, run_worker


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            k: (
                str(v)[:2] + "***" + str(v)[-3:]
                if k in {"account", "expected_account_id"} and v
                else redact(v)
            )
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def parser() -> Any:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--db")
    sub = result.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("bootstrap-profile")
    cmd.add_argument("--profile-id", required=True)
    cmd.add_argument("--environment", choices=["PAPER", "LIVE"], required=True)
    cmd.add_argument("--host", default="127.0.0.1")
    cmd.add_argument("--port", required=True, type=int)
    cmd.add_argument("--client-id", required=True, type=int)
    for name in ("import-profile",):
        cmd = sub.add_parser(name)
        cmd.add_argument("--file", required=True)
    for name in ("activate-profile", "discover-accounts"):
        cmd = sub.add_parser(name)
        cmd.add_argument("--profile", required=True, help="Immutable profile revision")
        if name == "activate-profile":
            cmd.add_argument("--reason", required=True)
    cmd = sub.add_parser("probe")
    cmd.add_argument("--profile", required=True)
    cmd.add_argument("--symbol", default="MU")
    cmd.add_argument("--what-if", action="store_true")
    cmd.add_argument("--output")
    cmd = sub.add_parser("run-worker")
    cmd.add_argument("--once", action="store_true")
    sub.add_parser("status")
    cmd = sub.add_parser("inspect")
    cmd.add_argument("--execution", required=True)
    cmd = sub.add_parser("reconcile")
    cmd.add_argument("--execution", required=True)
    cmd = sub.add_parser("resume-exit")
    cmd.add_argument("--lot", required=True)
    cmd.add_argument("--reason", required=True)
    cmd = sub.add_parser("migrate")
    cmd.add_argument("--dry-run", action="store_true")
    cmd.add_argument("--backup")
    cmd = sub.add_parser("validate-paper")
    cmd.add_argument("--suite", required=True)
    cmd.add_argument("--profile", required=True)
    cmd.add_argument("--symbol", default="MU")
    cmd.add_argument("--session", choices=["RTH", "EXTENDED", "OVERNIGHT"], default="RTH")
    cmd.add_argument("--side", choices=["LONG", "SHORT"], default="LONG")
    cmd.add_argument("--arm", action="store_true")
    cmd = sub.add_parser("arm-suite")
    cmd.add_argument("--suite", required=True)
    cmd = sub.add_parser("import-fills")
    cmd.add_argument("--file", required=True)
    cmd.add_argument("--reason", required=True)
    cmd = sub.add_parser("retry-event")
    cmd.add_argument("--event", required=True, type=int)
    cmd.add_argument("--reason", required=True)
    cmd = sub.add_parser("set-endpoint")
    cmd.add_argument("--profile", required=True)
    cmd.add_argument("--host", required=True)
    cmd.add_argument("--port", type=int, required=True)
    cmd.add_argument("--reason", required=True)
    cmd = sub.add_parser("rebase-position")
    cmd.add_argument("--profile", required=True)
    cmd.add_argument("--con-id", type=int, required=True)
    cmd.add_argument("--external-quantity", required=True)
    cmd.add_argument("--reason", required=True)
    cmd = sub.add_parser("adjust-lot")
    cmd.add_argument("--lot", required=True)
    cmd.add_argument("--adjustment-id", required=True)
    cmd.add_argument("--quantity-delta", required=True, type=int)
    cmd.add_argument("--effective-at", required=True)
    cmd.add_argument("--reason", required=True)
    cmd = sub.add_parser("set-session")
    cmd.add_argument("--date", required=True)
    cmd.add_argument("--open-at")
    cmd.add_argument("--close-at")
    cmd.add_argument("--closed", action="store_true")
    cmd.add_argument("--reason", required=True)
    cmd = sub.add_parser("replan-exit")
    cmd.add_argument("--lot", required=True)
    cmd.add_argument("--reason", required=True)
    return result


def status(repo: Any) -> Any:
    with repo.journal.transaction() as db:
        return {
            "profiles": [
                dict(row)
                for row in db.execute(
                    "SELECT revision,profile_id,account,environment FROM te_profiles"
                )
            ],
            "active": None,
            "jobs": [
                dict(r)
                for r in db.execute("SELECT id,account,ticker,leg,state,due_at FROM te_jobs")
            ],
            "lots": [json.loads(r[0]) for r in db.execute("SELECT payload FROM te_lots")],
            "acceptance": [
                json.loads(r[0]) for r in db.execute("SELECT payload FROM te_acceptance_runs")
            ],
        }


def probe(repo: Any, profile: Any, *, symbol: Any, what_if: Any = False) -> Any:
    output = {
        "mode": "PAPER_WHATIF" if what_if else "READ_ONLY",
        "profile_id": profile.profile_id,
        "environment": profile.environment,
        "port": profile.port,
        "orders_submitted": 0,
    }
    with WriterLock(repo.journal.path) as guard:
        broker = IbkrSession(
            profile,
            event_sink=lambda k, v: repo.event(profile.expected_account_id, k, v),
            write_guard=guard.assert_owned,
        )
        try:
            broker.connect()
            output["handshake"] = {
                "server_version": broker.app.serverVersion(),
                "account": profile.expected_account_id,
            }
            if profile.environment == "LIVE":
                output["positions_read"] = broker.positions_snapshot()
                output["order_sync"] = "NOT_REQUESTED_LIVE_READONLY"
            else:
                output["sync"] = broker.sync()
            contract = broker.contract(symbol)
            output["contract"] = contract
            output["market_rules"] = broker.market_rules(contract)
            try:
                output["quote"] = broker.quote(contract, "BUY")
            except Exception as exc:
                output["quote_gap"] = str(exc)
            try:
                output["overnight_contract"] = broker.contract(symbol, "OVERNIGHT")
            except Exception as exc:
                output["overnight_gap"] = str(exc)
            if what_if:
                identity = repo.allocate_order_id(
                    profile.expected_account_id, profile.client_id, broker.next_id
                )
                try:
                    output["what_if"] = broker.what_if(contract, identity)
                except Exception as exc:
                    output["what_if"] = {"status": "UNAVAILABLE", "error": str(exc)}
            output["result"] = "PARTIAL" if output.get("quote_gap") else "PASS"
            output["diagnostic_codes"] = sorted({item["code"] for item in broker.errors})
        except Exception as exc:
            output["result"], output["error"] = "FAILED", str(exc)
        finally:
            broker.close()
    return redact(output)


def main() -> Any:
    args = parser().parse_args()
    path = Path(args.db or DoxAgentSettings().persistent_runtime_v2_sqlite_path)
    if args.command == "migrate":
        if args.dry_run:
            print(
                json.dumps(
                    {"database_exists": path.exists(), "execution_schema": 1, "writes": False}
                )
            )
            return
        if path.exists():
            if not args.backup or Path(args.backup).exists():
                raise ValueError("existing database requires new --backup path")
            Path(args.backup).parent.mkdir(parents=True, exist_ok=True)
            with (
                closing(sqlite3.connect(path)) as source,
                closing(sqlite3.connect(args.backup)) as target,
            ):
                source.backup(target)
    repo = ExecutionRepository(RuntimeJournal(path))
    output = {}
    if args.command == "bootstrap-profile":
        from .discovery import discover_accounts

        accounts = discover_accounts(args.host, args.port, args.client_id)
        if len(accounts) != 1:
            raise ValueError("multiple accounts: use explicit import-profile account binding")
        profile = ExecutionProfile(
            profile_id=args.profile_id,
            environment=args.environment,
            account_mode="PAPER" if args.environment == "PAPER" else "LIVE_CASH",
            host=args.host,
            port=args.port,
            client_id=args.client_id,
            expected_account_id=accounts[0],
            short_enabled=args.environment == "PAPER",
        )
        output = {
            "revision": repo.import_profile(profile),
            "account": accounts[0],
            "activated": False,
        }
    elif args.command == "import-profile":
        profile = ExecutionProfile.model_validate_json(Path(args.file).read_text(encoding="utf-8"))
        output = {"revision": repo.import_profile(profile)}
    elif args.command in {"activate-profile", "discover-accounts", "probe"}:
        profile = repo.profile(args.profile)
        if args.command == "probe":
            output = probe(repo, profile, symbol=args.symbol, what_if=args.what_if)
            if args.output:
                Path(args.output).parent.mkdir(parents=True, exist_ok=True)
                Path(args.output).write_text(
                    json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8"
                )
        else:
            from secrets import randbelow

            from .discovery import discover_accounts
            from .schema import account_fuse

            # A read-only probe must not contend with the long-lived order client's ID.
            accounts = discover_accounts(profile.host, profile.port, 100000 + randbelow(900000))
            account_fuse(profile, accounts)
            output = {"accounts": [a[:2] + "***" + a[-3:] for a in accounts]}
            if args.command == "activate-profile":
                repo.activate(args.profile, args.reason)
                output["active_revision"] = args.profile
    elif args.command == "run-worker":
        with WriterLock(path) as guard:
            executor = Executor(repo, write_guard=guard.assert_owned)
            asyncio.run(run_worker(executor, once=args.once))
    elif args.command == "status":
        output = status(repo)
        output["active"] = repo.journal.get("trade_execution", "active")
        output["gaps"] = repo.journal.values("execution_gaps") + repo.journal.values(
            "execution_event_gaps"
        )
    elif args.command == "inspect":
        from .metrics import attempt_metrics

        output = {
            "execution": repo.get("executions", args.execution),
            "lot": repo.get("lots", args.execution),
        }
        for leg in ("entry", "exit"):
            attempts = repo.attempts(args.execution + ":" + leg)
            fills = repo.fills(args.execution + ":" + leg)
            output[leg] = {
                "job": repo.get("jobs", args.execution + ":" + leg),
                "attempts": [{**a, "metrics": attempt_metrics(a, fills)} for a in attempts],
                "fills": fills,
            }
    elif args.command == "resume-exit":
        output = repo.resume_exit(args.lot, args.reason)
    elif args.command == "validate-paper":
        output = create_suite(
            repo,
            identity=args.suite,
            revision=args.profile,
            ticker=args.symbol,
            session=args.session,
            side=args.side,
            arm=args.arm,
        )
    elif args.command == "arm-suite":
        from doxagent.persistent_runtime_v2.journal import encode

        value = repo.get("acceptance_runs", args.suite)
        if not value or value.get("execution_id"):
            raise ValueError("suite unavailable or already started")
        if repo.profile(value["revision"]).environment != "PAPER":
            raise ValueError("PAPER_ONLY_ACCEPTANCE")
        value["armed"] = True
        with repo.journal.transaction() as db:
            db.execute(
                "UPDATE te_acceptance_runs SET payload=? WHERE id=?", (encode(value), args.suite)
            )
        output = value
    elif args.command == "reconcile":
        with WriterLock(path) as guard:
            executor = Executor(repo, write_guard=guard.assert_owned)
            try:
                execution = repo.require("executions", args.execution)
                broker = executor.broker(execution["profile_revision"])
                output = broker.sync()
                executor.drain_events()
            finally:
                executor.close()
    elif args.command == "import-fills":
        with WriterLock(path):
            executor = Executor(repo)
            values = json.loads(Path(args.file).read_text(encoding="utf-8"))
            for fill in values:
                repo.event(fill["account"], "fill", fill)
            executor.drain_events()
            repo.journal.set(
                "execution_imports",
                repo.journal.clock().isoformat(),
                {"reason": args.reason, "count": len(values)},
            )
        output = {"imported": len(values), "gaps": repo.journal.values("execution_event_gaps")}
    elif args.command == "adjust-lot":
        with WriterLock(path):
            repo.adjust_lot(
                args.lot,
                adjustment_id=args.adjustment_id,
                quantity_delta=args.quantity_delta,
                effective_at=args.effective_at,
                reason=args.reason,
            )
        output = {"lot": repo.require("lots", args.lot)}
    elif args.command in {"set-session", "replan-exit"}:
        from .operations import replan_exit, set_session

        with WriterLock(path):
            output = (
                replan_exit(repo, args.lot, args.reason)
                if args.command == "replan-exit"
                else set_session(
                    repo,
                    args.date,
                    open_at=args.open_at,
                    close_at=args.close_at,
                    closed=args.closed,
                    reason=args.reason,
                )
            )
    elif args.command == "retry-event":
        with WriterLock(path):
            with repo.journal.transaction() as db:
                row = db.execute("SELECT * FROM te_events WHERE id=?", (args.event,)).fetchone()
            if row is None:
                raise ValueError("event not found")
            Executor(repo).apply_event(row)
            with repo.journal.transaction() as db:
                db.execute(
                    "DELETE FROM runtime_values WHERE namespace='execution_event_gaps' AND key=?",
                    (str(args.event),),
                )
            repo.journal.set("execution_event_repairs", str(args.event), {"reason": args.reason})
        output = {"event_replayed": args.event}
    elif args.command == "set-endpoint":
        with WriterLock(path):
            profile = repo.profile(args.profile)
            changed = ExecutionProfile.model_validate(
                {**profile.model_dump(), "host": args.host, "port": args.port}
            )
            session = IbkrSession(changed)
            try:
                session.connect()
                repo.journal.set(
                    "execution_endpoints",
                    profile.expected_account_id,
                    {"endpoint": {"host": args.host, "port": args.port}, "reason": args.reason},
                )
                output = {"endpoint_updated": True}
            finally:
                session.close()
    elif args.command == "rebase-position":
        from .strategy import decimal

        profile = repo.profile(args.profile)
        external_quantity = decimal(args.external_quantity)
        with WriterLock(path):
            repo.journal.set(
                "execution_position_baselines",
                f"{profile.expected_account_id}:{args.con_id}",
                {
                    "quantity": str(external_quantity),
                    "reason": args.reason,
                    "at": repo.journal.clock().isoformat(),
                },
            )
        output = {"external_baseline_updated": True}
    print(json.dumps(redact(output), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
