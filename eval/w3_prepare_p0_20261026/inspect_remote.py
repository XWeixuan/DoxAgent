"""Read-only incident inventory, no SDK initialization or business mutation."""
import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path


def rows(db, query, params=()):
    return [dict(r) for r in db.execute(query, params)]


now = datetime.now(UTC)
out = {"at": now.isoformat(), "release": os.getenv("DOXAGENT_BUILD_COMMIT")}
db = sqlite3.connect("file:/data/workspaces/worker-jobs.sqlite3?mode=ro", uri=True, timeout=3)
db.row_factory = sqlite3.Row
db.execute("PRAGMA query_only=ON")
out["slot_states"] = rows(db, "SELECT state,count(*) n FROM w3_ready_slots GROUP BY state")
out["slots"] = rows(db, "SELECT * FROM w3_ready_slots ORDER BY created_at DESC LIMIT 65")
for slot in out["slots"]:
    config = json.loads(slot.pop("config"))
    slot["profile"] = config.get("profile")
    slot["parent"] = config.get("parent_thread_id")
out["operations"] = rows(db, "SELECT * FROM w3_thread_operations ORDER BY updated_at DESC LIMIT 65")
for op in out["operations"]:
    req = json.loads(op.pop("request"))
    op["request_identity"] = {k: req.get(k) for k in ("run_id", "record_id", "source_case_id", "parent_thread_id", "phase", "profile")}
out["active_jobs"] = rows(db, "SELECT id,status,json_extract(request,'$.task_phase') phase,json_extract(request,'$.model') model,json_extract(payload,'$.started_at') started_at,json_extract(payload,'$.execution_phase') execution_phase FROM jobs WHERE status IN ('queued','running')")
out["recent_jobs"] = rows(db, "SELECT id,status,json_extract(request,'$.task_phase') phase,json_extract(payload,'$.error_code') error_code,json_extract(payload,'$.error_message') error_message,json_extract(payload,'$.created_at') created_at FROM jobs ORDER BY sequence DESC LIMIT 30")
out["operations_schema"] = rows(db, "PRAGMA table_info(w3_thread_operations)")
out["resources"] = {k: Path('/proc', k).read_text() for k in ('loadavg','pressure/cpu','pressure/io','pressure/memory')}
out["safety"] = json.loads(Path(os.environ['DOXAGENT_SAFETY_STATE_PATH']).read_text()) if os.getenv('DOXAGENT_SAFETY_STATE_PATH') and Path(os.environ['DOXAGENT_SAFETY_STATE_PATH']).exists() else None
out["sources"] = {}
for name in ('capsules.py','capsule_child.py','startup_admission.py','thread_lifecycle.py','w3_ready.py','jobs.py','sdk_runtime.py'):
    p = Path('/app/src/doxagent/codex_worker')/name
    out['sources'][name] = hashlib.sha256(p.read_bytes()).hexdigest() if p.exists() else None
out['boot_records'] = []
for op in out['operations'][:25]:
    identity = op.get('capsule_id')
    if identity:
        directory = Path('/data/workspaces/capsules')/identity
        out['boot_records'].append({'identity': identity, **{name: json.loads((directory/name).read_text()) for name in ('owner.json','boot.json') if (directory/name).exists()}})
runtime = sqlite3.connect('file:/data/runtime/runtime.sqlite3?mode=ro', uri=True, timeout=3)
runtime.row_factory = sqlite3.Row
runtime.execute('PRAGMA query_only=ON')
since = (now-timedelta(days=3)).isoformat()
out['case_groups'] = rows(runtime, "SELECT status,technical_status,json_extract(payload_json,'$.error_code') error_code,count(*) n FROM runtime_v2_cases WHERE created_at>=? GROUP BY status,technical_status,json_extract(payload_json,'$.error_code')", (since,))
out['failed_cases'] = rows(runtime, "SELECT case_id,ticker,created_at,updated_at,status,json_extract(payload_json,'$.error_code') error_code,json_extract(payload_json,'$.error_message') error_message FROM runtime_v2_cases WHERE created_at>=? AND status IN ('FAILED','UNAVAILABLE') ORDER BY created_at DESC LIMIT 70", (since,))
print(json.dumps(out, ensure_ascii=False))
