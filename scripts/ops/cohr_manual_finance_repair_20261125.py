"""Idempotent, broker-free repair; stop Executor and acquire its writer lock first."""

import argparse
import hashlib
import json
from pathlib import Path

from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.trade_execution.capital import CapitalLedger
from doxagent.trade_execution.configuration import account_pin
from doxagent.trade_execution.manual_finance import attach
from doxagent.trade_execution.repository import ExecutionRepository
from doxagent.trade_execution.worker import WriterLock

GROUP = "trade:case_6db274e8e6ce40bb96a4aabc55c11b8d"
EXEC_IDS = ("0000e95f.6af6e960.01.01", "0002bd06.6af6c443.01.01")
ACTOR = "user-authorized-cohr-finance-capital-20261125"


def snapshot(repo, account):
    with repo.transaction(write=False) as db:
        lots = [
            dict(r)
            for r in db.execute(
                "SELECT * FROM te_lots WHERE id IN (?,?)", (GROUP, GROUP + ":hedge")
            )
        ]
        jobs = [
            dict(r)
            for r in db.execute("SELECT * FROM te_jobs WHERE execution_id=? ORDER BY id", (GROUP,))
        ]
        adjustments = [
            json.loads(r[0])
            for r in db.execute(
                "SELECT payload FROM te_lot_adjustments WHERE id IN (?,?) ORDER BY id",
                tuple("manual-close-20261113:" + e for e in EXEC_IDS),
            )
        ]
        groups = CapitalLedger.groups(db, account)
        settings = db.execute(
            "SELECT payload FROM runtime_values "
            "WHERE namespace='execution_account_limits' AND key=?",
            (account,),
        ).fetchone()
        book = CapitalLedger(repo).book(account, db=db)
        return dict(
            lots=lots,
            jobs=jobs,
            adjustments=adjustments,
            group=repo.require("executions", GROUP)["group_state"],
            book={k: str(v) if not isinstance(v, bool) else v for k, v in book.items()},
            settings=json.loads(settings[0]) if settings else None,
            active_budgets=[{k: g.get(k) for k in ("id", "ticker", "budget_usd")} for g in groups],
            unsettled=db.execute(
                "SELECT count(*) FROM te_attempts WHERE account=? "
                "AND state NOT IN ('SETTLED','NOT_SENT')",
                (account,),
            ).fetchone()[0],
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="/data/runtime/runtime.sqlite3", type=Path)
    parser.add_argument(
        "--evidence",
        default="/data/runtime/acceptance/manual-close-cohr-20261113/before.json",
        type=Path,
    )
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    journal = RuntimeJournal(args.db, initialize=False)
    repo = ExecutionRepository(journal, initialize=False)
    with repo.transaction(write=False) as db:
        pin = account_pin(db, "LIVE")
    assert pin["profile"]["account_type"] == "MARGIN"
    account = pin["profile"]["expected_account_id"]
    assert repo.require("executions", GROUP)["account"] == account
    raw = args.evidence.read_bytes()
    evidence = json.loads(raw)
    fills = {f["exec_id"]: f for f in evidence["fills"] if f["exec_id"] in EXEC_IDS}
    assert set(fills) == set(EXEC_IDS)
    # Evidence archive is immutable and its hash is retained in each annotation.
    ref = "manual-close-cohr-20261113/before.json sha256=" + hashlib.sha256(raw).hexdigest()

    def perform():
        before = snapshot(repo, account)
        assert len(before["lots"]) == 2 and len(before["adjustments"]) == 2
        assert all(json.loads(lot["payload"])["remaining_qty"] == 0 for lot in before["lots"])
        assert before["group"] == "CLOSED" and before["unsettled"] == 0
        if args.apply:
            for exec_id in EXEC_IDS:
                attach(
                    repo,
                    "manual-close-20261113:" + exec_id,
                    fills[exec_id],
                    actor=ACTOR,
                    evidence=ref,
                )
            ledger = CapitalLedger(repo)
            ledger.allocate(
                account,
                "4000",
                "allocation:live-4000-20261125",
                "User: LIVE principal USD4000",
                actor=ACTOR,
            )
            if before["settings"].get("max_concurrent_tickers") != 2:
                ledger.set_ticker_limit(
                    account, 2, "User: two fixed MARGIN ticker slots", actor=ACTOR
                )
        after = snapshot(repo, account)
        assert before["lots"] == after["lots"] and before["jobs"] == after["jobs"]
        result = dict(
            applied=args.apply,
            at=journal.clock().isoformat(),
            before=before,
            after=after,
            lots_jobs_unchanged=True,
            broker_orders=0,
        )
        if args.receipt:
            args.receipt.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            if args.receipt.exists():
                raise ValueError("receipt already exists; use a distinct verification receipt")
            args.receipt.write_text(json.dumps(result, indent=2), encoding="utf-8")
            args.receipt.chmod(0o600)
        # Private account/lot payloads stay in the local receipt, not console.
        print(
            json.dumps(
                {k: result[k] for k in ("applied", "at", "lots_jobs_unchanged", "broker_orders")}
                | dict(book=after["book"], max_tickers=after["settings"]["max_concurrent_tickers"])
            )
        )

    if args.apply:
        with WriterLock(args.db):
            perform()
    else:
        perform()


if __name__ == "__main__":
    main()
