"""Explicit release migration; workers never perform these DDL operations."""

import json
import sys
from datetime import datetime, timedelta
from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.schema import utc_now
from .store import AcquisitionStore, path_for


def install(settings):
    from doxagent.message_bus_v2.repository import MessageBusV2Repository
    from doxagent.message_bus_v2.distribution_repository import DistributionRepository
    from doxagent.v2_read.outbox import SourceOutbox

    repo = MessageBusV2Repository(settings.message_bus_v2_sqlite_path, initialize=True)
    DistributionRepository(repo)
    store = AcquisitionStore(path_for(repo.path), initialize=True)
    # Only transport subscriptions change. No source epoch reset or history replay.
    from ..live_repository import LiveRepository
    LiveRepository(repo).install()
    SourceOutbox(repo.path, "bus").migrate()
    store.meta("schema", {"version": 2, "installed_at": utc_now().isoformat()})
    return {"schema": 2, "path": str(store.path)}


def main():
    settings = DoxAgentSettings()
    if len(sys.argv) > 1 and sys.argv[1] == "health":
        store=AcquisitionStore(path_for(settings.message_bus_v2_sqlite_path))
        cfg = store.get_meta(
            "configuration", {}
        )
        if not cfg.get("at") or utc_now() - datetime.fromisoformat(cfg["at"]) > timedelta(
            seconds=60
        ):
            raise SystemExit("collector configuration refresh is stale")
        health=store.health()
        print(json.dumps(health))
        if health['required_units'] and len(health['stalled_units'])==health['required_units']:raise SystemExit(1)
        return
    print(json.dumps(install(settings)))


if __name__ == "__main__":
    main()
