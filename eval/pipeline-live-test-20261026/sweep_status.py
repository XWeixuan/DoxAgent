"""Small operator receipt for the two diagnosed closed-sweep sources."""
import json
from datetime import UTC, datetime

from doxagent.message_bus_v2.collection_override import CollectionControl
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.settings import DoxAgentSettings

settings = DoxAgentSettings()
journal = RuntimeJournal(settings.persistent_runtime_v2_sqlite_path)
repo = MessageBusV2Repository(settings.message_bus_v2_sqlite_path)
tasks = journal.tasks(kind="SOURCE_SWEEP", active_only=False)
selected = []
for task in tasks:
    if task["inputs"]["source"]["source_id"] not in {
        "globenewswire_search", "reuters_site_search"
    } or "2026-10-25" not in task["id"]:
        continue
    receipt = task["receipt"]
    selected.append({
        "id": task["id"], "status": task["status"], "due_at": task["due_at"],
        "updated_at": task["updated_at"], "poll_done": receipt.get("poll_done"),
        "coverage": receipt.get("coverage"), "job_count": len(receipt.get("job_ids", [])),
        "page_coverage_count": len(receipt.get("page_coverages", [])),
    })
with repo._connect() as db:
    backlog = [dict(row) for row in db.execute(
        "SELECT status,count(*) captures,sum(message_count-intake_position) inputs "
        "FROM poll_captures WHERE status IN ('PENDING','RUNNING') GROUP BY status"
    )]
print(json.dumps({"at": datetime.now(UTC).isoformat(), "control": CollectionControl(repo).status(),
                  "sweeps": selected, "backlog": backlog}))
