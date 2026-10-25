"""Recover the single verified claim predating the replaced production Bus."""
import json
from datetime import datetime

from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import AuditRecord, UpdateActor
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.settings import DoxAgentSettings

settings = DoxAgentSettings()
journal = RuntimeJournal(settings.persistent_runtime_v2_sqlite_path)
identity = "sweep:CEG:2026-10-25:source:CEG:globenewswire_search"
# Verified aa5cdfa3 container startup, with its prior container already removed.
replaced_at = datetime.fromisoformat("2026-10-25T21:21:29.770657+00:00")
task = journal.get_task(identity)
result = {"task_id": identity, "recovered": False}
if task and task["status"] == "RUNNING" and datetime.fromisoformat(task["updated_at"]) < replaced_at:
    with journal.transaction() as db:
        row = journal.fence(db, task)
        if datetime.fromisoformat(row["updated_at"]) >= replaced_at:
            raise RuntimeError("claim has changed since verified container replacement")
        db.execute("UPDATE runtime_tasks SET status='PENDING',due_at=? WHERE id=?",
                   (journal.clock().isoformat(), identity))
    result.update(recovered=True, prior_owner=task["owner"], prior_token=task["token"],
                  prior_updated_at=task["updated_at"], prior_lease_until=task["lease_until"])
    MessageBusV2Repository(settings.message_bus_v2_sqlite_path).save_audit(AuditRecord(
        entity_type="runtime_task", entity_id=identity, actor=UpdateActor.SYSTEM,
        action="replaced_bus_claim_recovered", reason="pipeline-test-20261026 verified removed owner",
        payload=result,
    ))
print(json.dumps(result))
