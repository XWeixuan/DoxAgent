import asyncio
from datetime import datetime, timedelta
from decimal import Decimal

from doxagent.v2_read.metrics import Metrics
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.projector import ProjectionWorker
from doxagent.v2_read.projectors import DomainProjectors
from doxagent.v2_read.repository import ReadStore
from tests.test_trade_execution import run_job, setup  # noqa: F401


def test_exit_pnl_uses_owned_cost_and_revises_original_day_for_commissions(setup, tmp_path):  # noqa: F811
    clock, repo, revision, executor, broker = setup
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.ingest(
        "fixture",
        "provenance",
        [
            {
                "kind": "business_provenance",
                "ticker": "MU",
                "id": "case-a",
                "data": {"basis": "TEST_FIXTURE"},
            }
        ],
    )
    source = SourceOutbox(repo.journal.path, "runtime")
    source.migrate()
    worker = ProjectionWorker(store, [source], DomainProjectors(store))
    repo.admit(
        {
            "intent_id": "trade:A",
            "case_id": "case-a",
            "ticker": "MU",
            "status": "READY",
            "released_at": clock().isoformat(),
            "expires_at": (clock() + timedelta(hours=12)).isoformat(),
            "trade": {"decision": "LONG"},
            "execution_pin": {
                "revision": revision,
                "profile": repo.profile(revision).model_dump(mode="json"),
            },
        }
    )
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    clock.value = datetime.fromisoformat(repo.get("lots", "trade:A")["scheduled_exit"])
    asyncio.run(run_job(executor, repo, clock, "trade:A:exit"))

    def project():
        for _ in range(12):
            worker.tick(limit=500)
            with store.connect(write=True) as db:
                db.execute("UPDATE gaps SET next_attempt_at=''")
            worker.repair()
        with store.connect() as db:
            assert db.execute("SELECT reason FROM gaps").fetchall() == []
            return store.highwater(db)

    seq = project()
    entry, exit_fill = repo.fills("trade:A:entry")[0], repo.fills("trade:A:exit")[0]
    gross = Decimal(exit_fill["quantity"]) * (Decimal(exit_fill["price"]) - Decimal(entry["price"]))
    metrics = Metrics(store)
    assert metrics.value("paper_realized_net_pnl", ["MU"], seq, days=None) == gross
    assert metrics.metric("paper_realized_net_pnl", ["MU"], seq, days=None)["provisional"]
    clock.advance(86400 * 3)
    account = repo.profile(revision).expected_account_id
    for fill in (entry, exit_fill):
        repo.event(
            account, "fees", {"exec_id": fill["exec_id"], "currency": "USD", "commission": "1.25"}
        )
    latest = project()
    assert metrics.value("paper_realized_net_pnl", ["MU"], latest, days=None) == gross - Decimal(
        "2.50"
    )
    assert metrics.value("paper_realized_net_pnl", ["MU"], seq, days=None) == gross
    assert not metrics.metric("paper_realized_net_pnl", ["MU"], latest, days=None)["provisional"]
    assert broker.positions[9939] == "50"


def test_fifo_offset_realizes_old_lot_and_late_commission(setup, tmp_path):  # noqa: F811
    clock, repo, revision, executor, broker = setup
    store = ReadStore(tmp_path / "offset-read.db")
    store.migrate()
    store.ingest(
        "fixture",
        "provenance",
        [
            {
                "kind": "business_provenance",
                "ticker": "MU",
                "id": case_id,
                "data": {"basis": "TEST_FIXTURE"},
            }
            for case_id in ("case-a", "case-b")
        ],
    )
    source = SourceOutbox(repo.journal.path, "runtime")
    source.migrate()
    worker = ProjectionWorker(store, [source], DomainProjectors(store))

    for identity, case_id, side in (("trade:A", "case-a", "LONG"), ("trade:B", "case-b", "SHORT")):
        repo.admit(
            {
                "intent_id": identity,
                "case_id": case_id,
                "ticker": "MU",
                "status": "READY",
                "released_at": clock().isoformat(),
                "expires_at": (clock() + timedelta(hours=12)).isoformat(),
                "trade": {"decision": side},
                "execution_pin": {
                    "revision": revision,
                    "profile": repo.profile_payload(revision),
                },
            }
        )
        # Historical FIFO records predate virtual lot ownership and today's
        # per-ticker grants. Replay their recorded fill, not a new trading rule.
        if identity == "trade:A":
            asyncio.run(run_job(executor, repo, clock, identity + ":entry"))
        with repo.transaction() as db:
            import json

            execution = repo.require("executions", identity)
            execution["ownership_version"] = 1
            db.execute(
                "UPDATE te_executions SET payload=? WHERE id=?", (json.dumps(execution), identity)
            )
        if identity != "trade:A":
            first = repo.fills("trade:A:entry")[0]
            with repo.transaction() as db:
                row = dict(db.execute("SELECT * FROM te_attempts LIMIT 1").fetchone())
                attempt = json.loads(row["payload"])
                attempt.update(
                    id="historical-offset",
                    job_id="trade:B:entry",
                    side="SELL",
                    order_id=999,
                    order_ref="historical-offset",
                )
                row.update(
                    id=attempt["id"],
                    job_id=attempt["job_id"],
                    order_id=999,
                    order_ref="historical-offset",
                    payload=json.dumps(attempt),
                )
                db.execute(
                    "INSERT INTO te_attempts ("
                    + ",".join(row)
                    + ") VALUES ("
                    + ",".join("?" for _ in row)
                    + ")",
                    tuple(row.values()),
                )
            repo.apply_fill(
                {
                    **first,
                    "exec_id": "historical-offset.01",
                    "attempt_id": "historical-offset",
                    "order_id": 999,
                    "order_ref": "historical-offset",
                    "side": "SELL",
                    "quantity": "202",
                    "time": clock().isoformat(),
                },
                first["exit_at"],
            )
    assert repo.require("lots", "trade:A")["remaining_qty"] == 0

    def project():
        for _ in range(12):
            worker.tick(limit=500)
            with store.connect(write=True) as db:
                db.execute("UPDATE gaps SET next_attempt_at=''")
            worker.repair()
        with store.connect() as db:
            assert db.execute("SELECT reason FROM gaps").fetchall() == []
            return store.highwater(db)

    initial = project()
    metrics = Metrics(store)
    assert metrics.value("paper_realized_net_pnl", ["MU"], initial, days=None) == 0
    entry = repo.fills("trade:A:entry")[0]
    offset = repo.fills("trade:B:entry")[0]
    for fill in (entry, offset):
        repo.event(
            repo.profile(revision).expected_account_id,
            "fees",
            {"exec_id": fill["exec_id"], "currency": "USD", "commission": "1.25"},
        )
    latest = project()
    expected = -Decimal("1.25") - Decimal("1.25") * Decimal(198) / Decimal(202)
    assert metrics.value("paper_realized_net_pnl", ["MU"], latest, days=None) == expected
    assert not metrics.metric("paper_realized_net_pnl", ["MU"], latest, days=None)["provisional"]
