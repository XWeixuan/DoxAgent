import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

def run(*cmd):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=25)
    return {"exit": p.returncode, "stdout": p.stdout, "stderr": p.stderr}

names = ["doxagent-v2-v2-projector-1", "doxagent-v2-v2-maintenance-1", "doxagent-v2-v2-scheduler-1", "doxagent-v2-v2-api-1", "doxagent-v2-v2-transport-maintenance-1"]
out = {"at": datetime.now(timezone.utc).isoformat(), "cpus": os.cpu_count(), "containers": []}
for name in names:
    d = json.loads(run("docker", "inspect", name)["stdout"])[0]
    out["containers"].append({"name": name, "image": d["Image"], "pid": d["State"]["Pid"], "started": d["State"]["StartedAt"], "cmd": d["Config"]["Cmd"], "mounts": d["Mounts"], "memory_limit": d["HostConfig"]["Memory"], "nano_cpus": d["HostConfig"]["NanoCpus"]})
out["resources"] = run("sh", "-c", "uptime; free -m; df -h /; cat /proc/pressure/io; cat /proc/pressure/cpu; cat /proc/pressure/memory")
out["stats"] = run("docker", "stats", "--no-stream", "--format", "{{json .}}")
for role in ("projector", "maintenance", "transport-maintenance"):
    out[role + "_logs"] = run("docker", "logs", "--since", "20m", "--tail", "50", "doxagent-v2-v2-" + role + "-1")
print(json.dumps(out))
