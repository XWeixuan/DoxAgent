"""One finite kernel-only observation window; no ptrace, signals or DB access."""
import json
import os
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path


def read(path):
    try:
        return Path(path).read_text()
    except OSError:
        return ""


roles = {}
names = ["doxagent-v2-" + role + "-1" for role in ("codex-worker", "v2-scheduler", "v2-projector")]
for c in json.loads(subprocess.check_output(["docker", "inspect", *names])):
    pid = c["State"]["Pid"]
    cg = next(v.split(":", 2)[2] for v in read(f"/proc/{pid}/cgroup").splitlines() if v.startswith("0::"))
    roles[c["Name"]] = Path("/sys/fs/cgroup") / cg.lstrip("/")


def sample():
    out = {"at": datetime.now(UTC).isoformat(), "roles": {}, "processes": []}
    out["psi"] = {p: read("/proc/pressure/" + p) for p in ("cpu", "io", "memory")}
    out["swap_pages"] = {k: int(v) for k, v in (line.split() for line in read("/proc/vmstat").splitlines()) if k in ("pswpin", "pswpout")}
    for role, cg in roles.items():
        out["roles"][role] = {"cpu": read(cg / "cpu.stat"), "io": read(cg / "io.stat"), "memory": read(cg / "memory.current")}
        for pid in read(cg / "cgroup.procs").split()[:256]:
            raw = read(f"/proc/{pid}/stat")
            if not raw:
                continue
            fields = raw.rsplit(")", 1)[1].split()
            status = read(f"/proc/{pid}/status")
            namespace_pid = next((v.split()[1:] for v in status.splitlines() if v.startswith("NSpid:")), [])
            io = {k: int(v.strip()) for k, v in (line.split(":", 1) for line in read(f"/proc/{pid}/io").splitlines()) if k in ("read_bytes", "write_bytes")}
            out["processes"].append({
                "pid": int(pid), "namespace_pid": namespace_pid, "role": role,
                "name": raw.split("(", 1)[1].rsplit(")", 1)[0],
                "state": fields[0], "minor_faults": int(fields[7]), "major_faults": int(fields[9]),
                "cpu_ticks": int(fields[11]) + int(fields[12]), "start_ticks": int(fields[19]),
                "rss_pages": int(fields[21]), "schedstat": read(f"/proc/{pid}/schedstat"), **io,
            })
    return out


result = {"clock_ticks": os.sysconf("SC_CLK_TCK"), "page_size": os.sysconf("SC_PAGE_SIZE"),
          "schedstats_enabled": read("/proc/sys/kernel/sched_schedstats").strip(), "samples": []}
for i in range(21):
    result["samples"].append(sample())
    if i < 20:
        time.sleep(1)
print(json.dumps(result))
