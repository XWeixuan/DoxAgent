from datetime import UTC, datetime

from doxagent.api_v2.dto import validate
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.v2_control.repository import ControlRepository
from doxagent.v2_read.outbox import TABLES, SourceOutbox
from doxagent.v2_read.projector import ProjectionWorker
from doxagent.v2_read.projectors import DomainProjectors
from doxagent.v2_read.repository import ReadStore
from tests.test_persistent_runtime_v2 import _source
from tests.test_runtime_orchestration_execution import runtime_at
from tests.v2_backend.test_control import start


def test_runtime_case_projection_uses_real_native_output_and_recovers_source_order(tmp_path):
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    runtime, journal = runtime_at(tmp_path, [datetime(2026, 9, 8, 12, tzinfo=UTC)])
    control = ControlRepository(journal)
    control.migrate()
    source = SourceOutbox(journal.path, "runtime")
    source.migrate()
    start(control)
    mapper = DomainProjectors(store)
    worker = ProjectionWorker(store, [source], mapper)
    try:
        case = runtime.execute_message(_source())
        worker.tick(limit=500)
        assert store.get("case", "MU", case.case_id) is None
        # Arrival order is arbitrary across source databases. Register the missing exact source.
        store.ingest(
            "fixture",
            "definition",
            [
                {
                    "kind": "native:source_definitions",
                    "ticker": "",
                    "id": case.source.source_id,
                    "data": {"display_name": "Fixture API", "kind": "api"},
                }
            ],
        )
        with store.connect(write=True) as db:
            db.execute("UPDATE gaps SET next_attempt_at=''")
        worker.repair()
        value = store.get("case", "MU", case.case_id)
        validate("CaseSummary", value)
        assert value["member_count"] == case.source.member_count
        assert value["initial_route"] == case.route.primary_route.value
        assert value["results"] != ["TRADE_EXECUTION"]
        assert value["completed_at"]["value"] is None
    finally:
        runtime.close()


def test_all_native_bus_capture_tables_exist_and_triggers_are_idempotent(tmp_path):
    repository = MessageBusV2Repository(tmp_path / "bus.db")
    source = SourceOutbox(repository.path, "bus")
    selected = source.migrate()
    assert set(selected) == set(TABLES["bus"])
    source.migrate()
    service = MessageBusV2Service(repository)
    service.bootstrap()
    rows = source.read(0, 500)
    assert rows and any(row["table_name"] == "source_definitions" for row in rows)
