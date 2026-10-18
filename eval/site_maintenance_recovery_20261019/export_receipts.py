"""Export bounded, non-secret repair receipts; never copy Profile/DB/env backups."""

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

ROOT = Path("/var/lib/doxagent/site-maintenance-recovery-20261019")
REPO = Path("/home/ubuntu/doxagent")
SITE = "doxagent-v2-v2-site-access-1"
SUPERVISOR = "doxagent-v2-v2-chrome-supervisor-1"
COMMIT = "b1f8237c6dedd63cc5c2419811949886eda09c86"


def run(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=90, **kwargs).strip()


items = json.loads(run(["docker", "inspect", SITE, SUPERVISOR]))
volume = next(m["Source"] for m in items[0]["Mounts"] if m["Destination"] == "/site-data")
final = {
    "containers": [
        {
            "name": v["Name"],
            "id": v["Id"],
            "image": v["Image"],
            "tag": v["Config"]["Image"],
            "namespace": os.readlink(f"/proc/{v['State']['Pid']}/ns/net"),
            "health": v["State"].get("Health", {}).get("Status"),
        }
        for v in items
    ],
    "root_available_bytes": shutil.disk_usage("/").free,
}
final["network_guard"] = json.loads(
    run(["python3", "/usr/local/sbin/doxagent-site-access-network-guard"])
)
final["network_guard_timer"] = run(
    ["systemctl", "is-active", "doxagent-site-access-network-guard.timer"]
)
paths = run(
    [
        "git",
        "ls-tree",
        "-r",
        "--name-only",
        COMMIT,
        "--",
        "src/doxagent/site_strategy",
        "src/doxagent/message_bus_v2/yahoo_sources.py",
    ],
    cwd=REPO,
).splitlines()
final["source_provenance"] = []
for name in (SITE, SUPERVISOR):
    script = (
        "import json,hashlib; from pathlib import Path; paths="
        + repr(paths)
        + "; print(json.dumps({p:hashlib.sha256(Path('/app',p).read_bytes()).hexdigest() for p in paths}))"
    )
    actual = json.loads(run(["docker", "exec", name, "/app/.venv/bin/python", "-c", script]))
    mismatches = []
    for path in paths:
        source = subprocess.check_output(["git", "show", COMMIT + ":" + path], cwd=REPO)
        if hashlib.sha256(source).hexdigest() != actual[path]:
            mismatches.append(path)
    final["source_provenance"].append(
        {"container": name, "commit": COMMIT, "files": len(paths), "mismatches": mismatches}
    )
script = """import json,socket,urllib.request
result={'dns':{}}
for host in ['www.reuters.com','finance.yahoo.com']:
    result['dns'][host]=sorted({v[4][0] for v in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)})
result['readiness']=json.load(urllib.request.urlopen('http://127.0.0.1:8011/readyz',timeout=3))
print(json.dumps(result))
"""
final["site_checks"] = json.loads(
    run(["docker", "exec", "-i", SITE, "/app/.venv/bin/python", "-"], input=script)
)
final["vnc_bindings"] = items[1]["HostConfig"].get("PortBindings", {}).get("5900/tcp", [])
descriptor, archive = tempfile.mkstemp(
    prefix="site-maintenance-receipts-20261019-", suffix=".tar.gz"
)
os.close(descriptor)
with tempfile.TemporaryDirectory() as directory:
    out = Path(directory)
    (out / "host").mkdir()
    (out / "site").mkdir()
    for name in [
        "before.json",
        "before.initial.json",
        "after.json",
        "after.initial.json",
        "before-site-refinement.json",
        "after-site-refinement.json",
        "build.json",
        "build.initial.json",
        "build-site-refinement.json",
    ]:
        if (ROOT / name).exists():
            shutil.copy2(ROOT / name, out / "host" / name)
    for name in ("maintenance.json", "viewer.json", "yahoo.json", "remaining-challenges.json"):
        path = Path(volume) / "registry/site-maintenance-recovery-20261019" / name
        if path.exists():
            shutil.copy2(path, out / "site" / name)
    before = Path("/tmp/site-maintenance-before-20261019.json")
    if before.exists():
        shutil.copy2(before, out / "host/diagnostic.json")
    (out / "host/final.json").write_text(json.dumps(final, indent=2))
    with tarfile.open(archive, "w:gz") as bundle:
        for path in out.rglob("*.json"):
            bundle.add(path, arcname=str(path.relative_to(out)))
os.chmod(archive, 0o644)
print(
    json.dumps(
        {
            "archive": archive,
            "source_mismatches": sum(len(v["mismatches"]) for v in final["source_provenance"]),
            "guard": final["network_guard"],
            "root_available_bytes": final["root_available_bytes"],
        }
    )
)
