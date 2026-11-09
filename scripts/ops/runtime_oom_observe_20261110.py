"""Bounded 10-minute production observation; no historical payload expansion."""

import datetime
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT = Path("/var/lib/docker/volumes/doxagent-v2_v2-data/_data")
ROLES = [
    "codex-worker",
    "v2-scheduler",
    "v2-maintenance",
    "v2-o4",
    "v2-cdecr-executor",
    "v2-initialization",
    "initialization-guardian",
]


def snapshot():
    result = dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat())
    result["mem_kib"] = {
        k.strip(): int(v.split()[0])
        for k, v in (line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        if k in {"MemAvailable", "SwapFree", "SwapTotal", "AnonPages", "Cached"}
    }
    result["psi"] = {
        kind: Path("/proc/pressure", kind).read_text().strip() for kind in ["memory", "io", "cpu"]
    }
    result["disk"] = [
        line.split()
        for line in Path("/proc/diskstats").read_text().splitlines()
        if line.split()[2] == "vda"
    ]
    infos = json.loads(
        subprocess.check_output(["docker", "inspect", *["doxagent-v2-" + r + "-1" for r in ROLES]])
    )
    result["containers"] = {}
    for role, info in zip(ROLES, infos):
        item = dict(
            status=info["State"]["Status"],
            restarts=info["RestartCount"],
            oom=info["State"]["OOMKilled"],
        )
        if info["State"]["Running"]:
            pid = info["State"]["Pid"]
            cg = Path("/sys/fs/cgroup") / Path(
                "/proc", str(pid), "cgroup"
            ).read_text().strip().split("::")[-1].lstrip("/")
            for name in [
                "memory.current",
                "memory.swap.current",
                "pids.current",
                "memory.events",
                "pids.events",
                "io.stat",
                "cpu.stat",
            ]:
                try:
                    item[name] = (cg / name).read_text().strip()
                except OSError:
                    pass
        result["containers"][role] = item
        if role == "codex-worker" and info["State"]["Running"]:
            try:
                ip = next(
                    v["IPAddress"]
                    for v in info["NetworkSettings"]["Networks"].values()
                    if v.get("IPAddress")
                )
                env = dict(v.split("=", 1) for v in info["Config"]["Env"])
                request = urllib.request.Request(
                    "http://" + ip + ":8791/v1/resources",
                    headers={
                        "Authorization": "Bearer " + env["DOXAGENT_CODEX_WORKER_BEARER_TOKEN"]
                    },
                )
                with urllib.request.urlopen(request, timeout=3) as response:
                    result["worker_resources"] = json.load(response)
            except Exception as exc:
                result["worker_resources_error"] = type(exc).__name__
    # Active status uses jobs_status. Latest turns/cases are limited by rowid first.
    with sqlite3.connect(
        (ROOT / "workspaces/worker-jobs.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=1
    ) as db:
        result["jobs"] = [
            list(r)
            for r in db.execute(
                "SELECT status,count(*) FROM jobs WHERE status IN ('queued','running') GROUP BY status"
            )
        ]
    with sqlite3.connect(
        (ROOT / "runtime/runtime.sqlite3").as_uri() + "?mode=ro", uri=True, timeout=1
    ) as db:
        result["cases"] = [
            list(r)
            for r in db.execute(
                "SELECT rowid,case_id,ticker,status,updated_at,json_extract(payload_json,'$.error_code') FROM runtime_v2_cases WHERE rowid>(SELECT coalesce(max(rowid),0)-40 FROM runtime_v2_cases) ORDER BY rowid"
            )
        ]
    return result


def main():
    directory = Path(sys.argv[1])
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    seconds = int(sys.argv[2]) if len(sys.argv) > 2 else 600
    start = time.monotonic()
    with (directory / "observation.jsonl").open("a") as output:
        while True:
            try:
                value = snapshot()
            except Exception as exc:
                value = dict(
                    at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    error=type(exc).__name__,
                )
            value["elapsed_s"] = round(time.monotonic() - start, 2)
            output.write(json.dumps(value) + "\n")
            output.flush()
            print(
                json.dumps(
                    {
                        k: value[k]
                        for k in ["at", "elapsed_s", "mem_kib", "jobs", "error"]
                        if k in value
                    }
                ),
                flush=True,
            )
            if time.monotonic() - start >= seconds:
                break
            time.sleep(min(15, seconds - (time.monotonic() - start)))


if __name__ == "__main__":
    main()
