"""Read-only release inventory, no model, broker or business mutation."""

import json
import subprocess
from datetime import UTC, datetime


def inside(container, code):
    p = subprocess.run(
        ["docker", "exec", "-i", container, "python", "-"],
        input=code,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(p.stdout)


runtime_code = r"""
import json,sqlite3
from pathlib import Path
from doxagent.settings import DoxAgentSettings
s=DoxAgentSettings()
p=s.persistent_runtime_v2_sqlite_path
d=sqlite3.connect(Path(p).resolve().as_uri()+'?mode=ro',uri=True,timeout=.1)
d.row_factory=sqlite3.Row
d.execute('PRAGMA query_only=ON')
print(json.dumps(dict(path=str(p), tasks=[dict(r) for r in d.execute("SELECT id,ticker,kind,status,owner,token,lease_until,due_at FROM runtime_tasks WHERE kind='CASE' AND status IN ('PENDING','RUNNING') LIMIT 1000")],effects=[dict(r) for r in d.execute("SELECT effect_id,case_id,status,attempt_count FROM runtime_v2_effects WHERE status IN ('PENDING','PENDING_RETRY','RUNNING') LIMIT 1000")],cases=[dict(r) for r in d.execute("SELECT c.case_id,c.ticker,c.status,json_extract(c.payload_json,'$.decision_completed_at') AS decision_completed_at,json_extract(c.payload_json,'$.error_code') AS error_code FROM runtime_v2_cases c WHERE c.status IN ('RUNNING','PENDING_W3') ORDER BY c.created_at DESC LIMIT 1000")]),default=str))
"""
worker_code = r"""
import json,sqlite3
from pathlib import Path
from doxagent.settings import DoxAgentSettings
s=DoxAgentSettings()
p=Path('/data/workspaces/worker-jobs.sqlite3')
d=sqlite3.connect(p.resolve().as_uri()+'?mode=ro',uri=True,timeout=.1)
d.row_factory=sqlite3.Row
print(json.dumps(dict(active_jobs=[dict(r) for r in d.execute("SELECT id,status,json_extract(request,'$.node') AS node,json_extract(request,'$.task_phase') AS phase FROM jobs WHERE status IN ('queued','running')")],counts=dict(d.execute('SELECT status,count(*) FROM jobs GROUP BY status')))))
"""
infos = json.loads(
    subprocess.check_output(
        ["docker", "inspect", *subprocess.check_output(["docker", "ps", "-q"], text=True).split()],
        text=True,
    )
)
containers = {
    c["Config"]["Labels"].get("com.docker.compose.service"): dict(
        id=c["Id"],
        image=c["Image"],
        restart_count=c["RestartCount"],
        started_at=c["State"]["StartedAt"],
    )
    for c in infos
    if c["Config"]["Labels"].get("com.docker.compose.project") == "doxagent-v2"
}
result = dict(
    at=datetime.now(UTC).isoformat(),
    containers=containers,
    runtime=inside("doxagent-v2-v2-scheduler-1", runtime_code),
    worker=inside("doxagent-v2-codex-worker-1", worker_code),
)
print(json.dumps(result, ensure_ascii=False))
