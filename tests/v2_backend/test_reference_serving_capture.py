from doxagent.event_library.compiler import EventLibraryViewCompiler
from doxagent.event_library.contracts import ReferenceViewDeltaSnapshot
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.reference_capture import prepare, settle
from doxagent.v2_read.formal import FormalProjectors
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.repository import ReadStore
from tests.test_codex_event_library_incremental import _publish_v1


def test_serving_capture_keeps_reference_delivery_for_frontend_date_projection(tmp_path):
    root = tmp_path / "events"
    repository = EventLibraryRepository(root / "US/MU/event_library.sqlite3")
    _publish_v1(repository, tmp_path / "fixture")
    journal = RuntimeJournal(tmp_path / "runtime.db")
    source = SourceOutbox(journal.path, "runtime")
    source.migrate(tables=("runtime_values",))
    task = {"id": "recovered-day", "ticker": "MU", "inputs": {"control_epoch": 1}}
    prepare(
        journal,
        task,
        EventLibraryViewCompiler(repository),
        root,
        "2026-10-23",
        ReferenceViewDeltaSnapshot(
            ticker="MU", from_library_version=1, to_library_version=1, reference_view_delta=""
        ),
    )
    settle(journal, task["id"], "SUCCEEDED")
    with source.connection() as db:
        seq = db.execute(
            "SELECT max(seq) FROM v2_source_outbox WHERE table_name='runtime_values' "
            "AND entity_id=?",
            ('["reference_deliveries","recovered-day"]',),
        ).fetchone()[0]
    packet = source.event(seq, serving=True)
    assert not packet.get("serving_skipped")
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    records, metrics = FormalProjectors(store)({**packet, "source": "runtime"})
    store.ingest("runtime", str(seq), records, contributions=metrics)
    assert store.get("delta_day", "MU", "2026-10-23")["state"] == "NO_CHANGE"
    assert store.get("reference_delta", "MU", task["id"]) is not None
    # Snapshots are projected into page objects, not mirrored as large native values.
    assert store.get("native:runtime_values", "MU", "reference_deliveries:recovered-day") is None
