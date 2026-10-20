from datetime import UTC, datetime, timedelta

from doxagent.message_bus_v2.corporate_ir.repository import save_state
from doxagent.message_bus_v2.corporate_ir.schema import IrEndpointState
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.v2_read.maintenance import collect
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.projectors import DomainProjectors
from doxagent.v2_read.repository import ReadStore


def test_endpoint_cdc_and_snapshot_identity_are_distinct(tmp_path):
    repo = MessageBusV2Repository(tmp_path / "bus.db")
    outbox = SourceOutbox(repo.path, "bus")
    outbox.migrate()
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    mapper = DomainProjectors(store)
    for key in ["cohr.corporate", "cohr.financial"]:
        state = IrEndpointState(binding_id="COHR:corporate_ir_press_releases", endpoint_id=key)
        save_state(repo, state)
    events = [e for e in outbox.read(0) if e["table_name"] == "ir_endpoint_states"]
    assert len(events) == 2
    ids = []
    for event in events:
        for operation in ["INSERT", "BACKFILL"]:
            records, _ = mapper({**event, "operation": operation})
            assert records[0]["ticker"] == "COHR"
        ids.append(records[0]["id"])
        store.ingest("bus", event["entity_id"], records)
    assert len(set(ids)) == 2
    assert all(store.get("native:ir_endpoint_states", "COHR", key) for key in ids)


def test_endpoint_history_is_technical_and_current_survives_gc(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    now = datetime.now(UTC)
    old = (now - timedelta(days=5)).isoformat()
    for revision in range(4):
        store.ingest(
            "bus",
            str(revision),
            [
                {
                    "kind": "native:ir_endpoint_states",
                    "ticker": "COHR",
                    "id": "endpoint",
                    "data": {"revision": revision},
                }
            ],
            at=old,
        )
    with store.connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM sqlite_master WHERE name='business_revisions'"
            ).fetchone()[0]
            == 0
        )
    with store.connect(write=True) as db:
        db.execute("UPDATE commit_retention SET projected_at=?", (old,))
    for _ in range(4):
        collect(store, now=now, budget=1)
    assert store.get("native:ir_endpoint_states", "COHR", "endpoint")["revision"] == 3
    with store.connect() as db:
        assert (
            db.execute(
                "SELECT count(*) FROM object_history WHERE kind='native:ir_endpoint_states'"
            ).fetchone()[0]
            == 0
        )
        assert (
            db.execute(
                "SELECT count(*) FROM sqlite_master WHERE name='business_revisions'"
            ).fetchone()[0]
            == 0
        )
