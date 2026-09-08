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
