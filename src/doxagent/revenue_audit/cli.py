"""Explicit activation only: establish a new-data boundary, never backfill trades."""

import argparse
import json
from datetime import UTC, datetime

from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.trade_execution.capital import CapitalLedger
from doxagent.trade_execution.configuration import account_pin
from doxagent.trade_execution.repository import ExecutionRepository
from doxagent.v2_read.repository import ReadStore

from .projection import account_key, activation


def activate(store, journal, now=None):
    existing = activation(store)
    if existing:
        return existing
    opening = {}
    ledger = CapitalLedger(ExecutionRepository(journal, initialize=False))
    # One short administrative cutover: funds committed afterwards cannot also
    # appear in the opening baseline. Normal HTTP reads never take this lock.
    with journal.transaction() as db:
        since = (now or datetime.now(UTC)).astimezone(UTC).isoformat()
        for env in ("PAPER", "LIVE"):
            pin = account_pin(db, env)
            if pin:
                account = pin["profile"]["expected_account_id"]
                has_funding = db.execute(
                    "SELECT 1 FROM te_capital_events WHERE account=? AND kind='ALLOCATION' LIMIT 1",
                    (account,),
                ).fetchone()
                book = (
                    ledger.book(account, db=db)
                    if has_funding
                    else dict(principal=0, realized=0, provisional=False)
                )
                opening[env] = dict(
                    account_key=account_key(account),
                    capital=str(book["principal"] + book["realized"])
                    if not book["provisional"]
                    else None,
                )
        value = dict(since=since, opening=opening)
        with store.connect(write=True) as read_db:
            read_db.execute(
                "INSERT OR IGNORE INTO read_meta VALUES('revenue_audit_v1',?)", (json.dumps(value),)
            )
            return json.loads(
                read_db.execute(
                    "SELECT value FROM read_meta WHERE key='revenue_audit_v1'"
                ).fetchone()[0]
            )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--read-db", required=True)
    parser.add_argument("--runtime-db", required=True)
    parser.add_argument("command", choices=["activate"])
    args = parser.parse_args()
    print(
        json.dumps(
            activate(ReadStore(args.read_db), RuntimeJournal(args.runtime_db, initialize=False))
        )
    )


if __name__ == "__main__":
    main()
