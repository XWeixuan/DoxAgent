from datetime import UTC, datetime

from fastapi.testclient import TestClient

from doxagent.api_v2.app import PREFIX, create_app
from doxagent.event_library.compiler import EventLibraryViewCompiler
from doxagent.event_library.contracts import ReferenceViewDeltaSnapshot
from doxagent.event_library.repository import EventLibraryRepository
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.persistent_runtime_v2.reference_capture import prepare, settle
from doxagent.v2_control.repository import ControlRepository
from doxagent.v2_read.event_lifecycle import activate
from doxagent.v2_read.identities import migrate
from doxagent.v2_read.libraries import LibraryIndexer
from doxagent.v2_read.reference import project
from doxagent.v2_read.repository import ReadStore
from tests.test_codex_event_library_incremental import _publish_v1
from tests.v2_backend.test_api import OfflineAuth


def test_actual_reference_input_and_equal_version_no_change_are_immutable(tmp_path):
    root = tmp_path / "events"
    native = EventLibraryRepository(root / "US" / "MU" / "event_library.sqlite3")
    with native._write() as db:
        migrate(db)
    _publish_v1(native, root)
    compiler = EventLibraryViewCompiler(native)
    journal = RuntimeJournal(tmp_path / "runtime.db")
    control = ControlRepository(journal)
    control.migrate()
    task = {"id": "daily-a", "ticker": "MU", "inputs": {"control_epoch": 1}}
    prepare(
        journal,
        task,
        compiler,
        root,
        "2026-09-08",
        compiler.reference_view_delta("MU", from_version=0, to_version=1),
    )
    settle(journal, "daily-a", "SUCCEEDED")
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    records = project(store, journal.get("reference_deliveries", "daily-a"))
    seq = store.ingest("runtime", "reference", records)
    ref, indexed = LibraryIndexer(root).index("MU", 1)
    store.ingest("library", "v1", indexed)
    event_rows, counts = activate(store, "MU", {"event_library": ref}, "2026-09-08T14:00:00Z")
    seq = store.ingest("library", "active", event_rows, contributions=counts)
    view = store.save_token(
        "developer",
        "test",
        {
            "seq": seq,
            "as_of": "2026-09-08T15:00:00Z",
            "wire": {
                "page": "EVENTS",
                "ticker": "MU",
                "activation": {"data": {"event_library": ref}},
                "period": {"selected": "ALL", "previous": None},
            },
        },
        view=True,
        now=datetime.now(UTC),
    )
    with TestClient(create_app(store=store, control=control, auth=OfflineAuth())) as client:
        headers = {"Authorization": "Bearer offline"}
        for suffix in (
            "event-library/events",
            "event-library/index",
            "event-library/metrics",
            "reference-deltas/days",
        ):
            response = client.get(
                PREFIX + "/tickers/MU/" + suffix, headers=headers, params={"view_id": view}
            )
            assert response.status_code == 200, response.text
        response = client.get(
            PREFIX + "/tickers/MU/reference-deltas/days/2026-09-08",
            headers=headers,
            params={"view_id": view, "limit": 1},
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        change = data["changes"]["items"][0]
        url = PREFIX + f"/tickers/MU/reference-deltas/daily-a/changes/{change['change_id']}/event"
        response = client.get(url, headers=headers, params={"side": "after", "limit": 1})
        assert response.status_code == 200, response.text
        assert (
            response.json()["data"]["reference_snapshot_id"] == data["after_reference_snapshot_id"]
        )
        assert client.get(url, headers=headers, params={"side": "before"}).status_code == 404
    prepare(
        journal,
        {**task, "id": "daily-b"},
        compiler,
        root,
        "2026-09-09",
        ReferenceViewDeltaSnapshot(
            ticker="MU", from_library_version=1, to_library_version=1, reference_view_delta=""
        ),
    )
    settle(journal, "daily-b", "SUCCEEDED")
    store.ingest("runtime", "noop", project(store, journal.get("reference_deliveries", "daily-b")))
    assert store.get("delta_day", "MU", "2026-09-09")["state"] == "NO_CHANGE"
    assert store.get("delta_day", "MU", "2026-09-08", seq)["state"] == "AVAILABLE"


def test_same_version_membership_removal_and_daily_net_zero(tmp_path):
    from doxagent.persistent_runtime_v2.reference_capture import actual_delta, freeze_before

    root = tmp_path / "events"
    native = EventLibraryRepository(root / "US" / "MU" / "event_library.sqlite3")
    with native._write() as db:
        migrate(db)
    _publish_v1(native, root)
    compiler = EventLibraryViewCompiler(native)
    journal = RuntimeJournal(tmp_path / "runtime.db")
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    task = {"id": "first", "ticker": "MU", "inputs": {"control_epoch": 1}}
    prepare(
        journal,
        task,
        compiler,
        root,
        "2026-09-08",
        compiler.reference_view_delta("MU", from_version=0, to_version=1),
    )
    settle(journal, "first", "SUCCEEDED")
    store.ingest("runtime", "first", project(store, journal.get("reference_deliveries", "first")))
    task = {**task, "id": "second"}
    freeze_before(journal, task, compiler, 1)

    class RemovedMembership:
        def reference_events(self, ticker, version):
            return []

        def _render_reference_events(self, ticker, events):
            return compiler._render_reference_events(ticker, events)

    delta = actual_delta(journal, task, RemovedMembership(), 1, 1)
    assert delta.removed_event_ids
    assert delta.before_reference_snapshot_id != delta.after_reference_snapshot_id
    prepare(journal, task, RemovedMembership(), root, "2026-09-08", delta)
    settle(journal, "second", "SUCCEEDED")
    store.ingest("runtime", "second", project(store, journal.get("reference_deliveries", "second")))
    assert store.get("delta_day", "MU", "2026-09-08")["state"] == "NO_CHANGE"
    assert store.get("reference_input", "MU", "second")["before"]["events"]
