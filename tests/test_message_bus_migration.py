from __future__ import annotations

import sqlite3

from doxagent.message_bus_v2.migration import apply, preview
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import AcquisitionMode, UpdateActor
from doxagent.message_bus_v2.service import MessageBusV2Service


def test_migration_previews_backs_up_and_is_idempotent(tmp_path) -> None:
    path = tmp_path / "bus.sqlite3"
    repository = MessageBusV2Repository(path)
    service = MessageBusV2Service(repository)
    service.bootstrap()
    service.update_source(
        "reuters_site_search",
        {"acquisition_mode": AcquisitionMode.BY_TICKER},
        actor=UpdateActor.SYSTEM,
    )
    assert [item["source_id"] for item in preview(path)["source_changes"]] == [
        "reuters_site_search"
    ]
    result = apply(path)
    assert result["backup"] is not None
    assert result["after"]["source_changes"] == []
    with sqlite3.connect(result["backup"]) as backed_up:
        assert backed_up.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert apply(path)["after"]["source_changes"] == []
