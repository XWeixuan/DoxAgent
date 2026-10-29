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
    migrate_http_capacity(repo)
    SourceOutbox(repo.path, "bus").migrate()
    store.meta("schema", {"version": 2, "installed_at": utc_now().isoformat()})
    return {"schema": 2, "path": str(store.path)}


def migrate_http_capacity(repo):
    """Explicit release-only migration; preserve periods, parameters and rate gap."""
    from ..schema import UpdateActor
    with repo.transaction() as db:
        row=db.execute("SELECT version,data_json FROM source_definitions WHERE source_id='finnhub_company_news'").fetchone()
        if row is None:return
        from ..schema import SourceDefinition
        current=SourceDefinition.model_validate_json(row['data_json'])
        constraints=current.scheduler_constraints
        if constraints.capacity_mode!='fixed' or constraints.max_concurrency!=1:return
        updated=current.model_copy(update={'version':row['version']+1,
            'scheduler_constraints':constraints.model_copy(update={'capacity_mode':'adaptive','max_concurrency':2}),
            'updated_at':utc_now(),'updated_by':UpdateActor.SYSTEM,
            'updated_reason':'unified IO: two HTTP inflight requests; unchanged account pacing'})
        # The read and version transition share the owner writer transaction.
        repo._save_source_tx(db,updated)
        assert db.execute("SELECT version FROM source_definitions WHERE source_id=?",(current.source_id,)).fetchone()[0]==updated.version


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
