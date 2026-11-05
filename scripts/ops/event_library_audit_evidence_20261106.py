"""Read scalar scheduling/source evidence without constructing writable services."""

import hashlib
import json
import sqlite3
from pathlib import Path

import doxagent

FILES = [
    "event_library/compiler.py",
    "event_library/contracts.py",
    "event_library/repository.py",
    "event_library/reference_review.py",
    "event_library/validator.py",
    "workflows/codex_event_library/remote_runner.py",
    "persistent_runtime_v2/maintenance.py",
    "persistent_runtime_v2/coordinator.py",
    "persistent_runtime_v2/event_branch.py",
]

base = Path(doxagent.__file__).parent
result = {
    "source_hashes": {
        name: hashlib.sha256((base / name).read_bytes()).hexdigest() for name in FILES
    }
}
db = sqlite3.connect("file:/data/runtime/runtime.sqlite3?mode=ro", uri=True)
db.row_factory = sqlite3.Row
db.execute("PRAGMA query_only=ON")
result["tasks"] = [
    dict(row)
    for row in db.execute(
        "SELECT ticker,kind,status,count(*) AS n FROM runtime_tasks "
        "GROUP BY ticker,kind,status ORDER BY ticker,kind,status"
    )
]
result["recent_maintenance"] = [
    dict(row)
    for row in db.execute(
        "SELECT id,ticker,status,updated_at,json_extract(inputs,'$.scope') AS scope,"
        "json_extract(receipt,'$.frame.run_id') AS run_id "
        "FROM runtime_tasks WHERE kind='MAINTENANCE' ORDER BY updated_at DESC LIMIT 30"
    )
]
print(json.dumps(result))
