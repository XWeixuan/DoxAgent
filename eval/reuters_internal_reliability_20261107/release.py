"""Exact-SHA Site Access release; never rebuild Supervisor or mutate browser state."""
import argparse
import hashlib
import json
import os
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("commit")
args = p.parse_args()
repo = Path("/home/ubuntu/doxagent")
commit = subprocess.check_output(["git", "rev-parse", "--verify", args.commit + "^{commit}"], cwd=repo, text=True).strip()
assert commit == args.commit and len(commit) == 40
supervisor = "doxagent-v2-v2-chrome-supervisor-1"
site = "doxagent-v2-v2-site-access-1"


def container(name):
    value = json.loads(subprocess.check_output(["docker", "inspect", name]))[0]
    return {"id": value["Id"], "image": value["Image"], "started_at": value["State"]["StartedAt"],
            "health": value["State"].get("Health", {}).get("Status")}


before = container(supervisor)
dbpath = Path("/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3")
backup_root = Path("/var/lib/doxagent/releases/backups")
backup_root.mkdir(mode=0o700, parents=True, exist_ok=True)
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
backup = backup_root / ("reuters-internal-" + stamp + ".sqlite3")
fd = os.open(backup, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
os.close(fd)
with sqlite3.connect(dbpath.as_uri() + "?mode=ro", uri=True) as source:
    with sqlite3.connect(backup) as target:
        source.backup(target)
started = datetime.now(timezone.utc).isoformat()
log = Path("/var/lib/doxagent/releases/reuters-internal-" + stamp + ".log")
fd = os.open(log, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
with os.fdopen(fd, "w") as stream:
    code = subprocess.run(["python3", "scripts/ops/storage_release.py", "release", "--commit", commit,
                           "--component", "v2-site-access"], cwd=repo,
                          stdout=stream, stderr=subprocess.STDOUT).returncode
receipt = {"commit": commit, "started_at": started,
           "finished_at": datetime.now(timezone.utc).isoformat(), "returncode": code,
           "supervisor_unchanged": container(supervisor) == before, "site": container(site),
           "private_log": str(log), "backup": str(backup)}
files = ["datadome_solver.py", "external_runtime.py", "governance_repository.py", "human_verification.py",
         "recovery.py", "service.py", "worker_runtime.py", "targets.py", "runtime.py"]
files = ["site_strategy/" + f for f in files] + ["message_bus_v2/reuters_sources.py"]
if code == 0:
    expected = {f: hashlib.sha256(subprocess.check_output(
        ["git", "show", commit + ":src/doxagent/" + f], cwd=repo)).hexdigest() for f in files}
    script = "import hashlib,json; from pathlib import Path; print(json.dumps({f:hashlib.sha256(Path('/app/src/doxagent',f).read_bytes()).hexdigest() for f in " + repr(files) + "}))"
    actual = json.loads(subprocess.check_output(["docker", "exec", site, "python", "-c", script]))
    receipt["hashes"] = actual
    receipt["hashes_match"] = expected == actual
Path("/tmp/reuters-internal-release-receipt-20261107.json").write_text(json.dumps(receipt, indent=2))
print(json.dumps(receipt, indent=2))
raise SystemExit(code)
