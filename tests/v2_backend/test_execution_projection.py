import asyncio
import json
from datetime import UTC, datetime, timedelta

from doxagent.api_v2.dto import available, missing
from doxagent.api_v2.graph import Graphs
from doxagent.api_v2.views import Views
from doxagent.v2_read.calendar import PageCalendar
from doxagent.v2_read.metrics import Metrics
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.projector import ProjectionWorker
from doxagent.v2_read.projectors import DomainProjectors
from doxagent.v2_read.repository import ReadStore
from tests.test_trade_execution import run_job, setup  # noqa: F401 -- shared offline broker fixture


def test_owned_fill_and_late_commission_are_projected_without_order_status_inference(
    setup,  # noqa: F811 -- shared offline fixture
    tmp_path,
):
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
    case = {
        "case_id": "case-a",
        "revision": 1,
        "semantic_day": "2026-09-08",
        "runtime_mode": "REALTIME",
        "first_round_shape": "PARALLEL",
        "status": "COMPLETED",
        "technical_status": "OK",
        "title": available("News"),
        "source": {"source_id": "news", "binding_id": "MU:news", "name": "News", "kind": "api"},
        "received_at": "2026-09-08T12:00:00Z",
        "completed_at": missing(),
        "duration_seconds": missing(),
        "initial_route": "TRADE",
        "resolved_route": None,
        "w3_status": None,
        "final_novelty": available("NEW"),
        "final_policy_hit": available(True),
        "results": ["EVENT_DISCOVERY"],
        "result_settled": True,
        "trade_disposition": "READY",
        "stream_item_id": "stream-a",
        "member_count": 1,
    }
    store.ingest(
        "fixture", "case", [{"kind": "case", "ticker": "MU", "id": "case-a", "data": case}]
    )
    repo.admit(
        {
            "intent_id": "trade:A",
            "ticker": "MU",
            "case_id": "case-a",
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
    worker.tick(limit=500)
    summary = store.get("execution", "MU", "trade:A")
    assert summary["intake_status"] == "EXECUTION_ACCEPTED"
    assert summary["has_actual_fill"] is False
    assert store.get("case", "MU", "case-a")["result_settled"] is False
    asyncio.run(run_job(executor, repo, clock, "trade:A:entry"))
    for _ in range(5):
        worker.tick(limit=500)
    summary = store.get("execution", "MU", "trade:A")
    assert summary["has_actual_fill"] is True
    assert summary["filled_quantity"]["value"] == "198"
    fill = repo.fills("trade:A:entry")[0]
    repo.event(
        repo.profile(revision).expected_account_id,
        "fees",
        {"exec_id": fill["exec_id"], "currency": "USD", "commission": "1.25"},
    )
    worker.tick(limit=500)
    with store.connect() as db:
        seq = store.highwater(db)
        assert db.execute("SELECT count(*) FROM gaps").fetchone()[0] == 0
    rows = store.page("fill", "MU", seq, parent="trade:A")
    assert rows[0]["data"]["commission_usd"]["value"] == "1.25"
    assert rows[0]["data"]["leg"] == "ENTRY"
    assert "TRADE_EXECUTION" in store.get("case", "MU", "case-a")["results"]
    wire = {"page": "RUNTIME", "ticker": "MU", "period": {"selected": "ALL"}}
    view_id = store.save_token(
        "developer", "graph-test", {"wire": wire, "seq": seq}, view=True, now=datetime.now(UTC)
    )
    graphs = Graphs(store, Views(store, PageCalendar()))
    baseline = graphs.baseline("developer", "MU", view_id)
    assert (
        next(n for n in baseline["nodes"] if n["node_id"] == "TRADE_EXECUTION")["case_count"] == 1
    )
    state = graphs.cursor("developer", baseline["stream_cursor"], "MU", view_id)
    family, separator, revision_number = fill["exec_id"].rpartition(".")
    corrected_id = (family if separator and revision_number.isdigit() else fill["exec_id"]) + ".99"
    assert repo.apply_fill({**fill, "exec_id": corrected_id, "quantity": "0"}, fill["exit_at"])
    for _ in range(5):
        worker.tick(limit=500)
    corrected = store.get("case", "MU", "case-a")
    assert corrected["results"] == ["EVENT_DISCOVERY"]
    with store.connect() as db:
        latest = store.highwater(db)
        assert db.execute("SELECT count(*) FROM gaps").fetchone()[0] == 0
    assert Metrics(store).value("executed_cases", ["MU"], latest, days=None) == 0
    assert Metrics(store).value("executed_cases", ["MU"], seq, days=None) == 1
    while update := graphs.next("developer", state, {"wire": wire}):
        text, state = update
        payload = json.loads(
            next(line[6:] for line in text.splitlines() if line.startswith("data: "))
        )["payload"]
    assert (
        next(n for n in payload["replace_nodes"] if n["node_id"] == "TRADE_EXECUTION")["case_count"]
        == 0
    )
    assert state["seq"] > seq
