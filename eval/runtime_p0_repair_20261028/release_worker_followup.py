"""Final Worker/Scheduler boundary patch; canonical targeted release."""

import copy
import hashlib
import importlib.util
import json
import subprocess
import sys
import time
from pathlib import Path

repo = Path("/home/ubuntu/doxagent")
root = Path("/var/lib/doxagent/releases")
commit = sys.argv[1]
spec = importlib.util.spec_from_file_location("release", repo / "scripts/ops/storage_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

with release.lock(root):
    before = {
        c["Config"]["Labels"].get("com.docker.compose.service"): c["Id"]
        for c in release.inspect_containers()
    }
    built = release.build(repo, commit, root, ["backend"])
    state = json.loads((root / "state.json").read_text())
    config = json.loads((root / "compose.current.json").read_text())
    selected = {}
    for name in ("codex-worker", "v2-scheduler"):
        value = copy.deepcopy(config["services"][name])
        value["image"] = built["images"]["backend"]["tag"]
        value.setdefault("environment", {})["DOXAGENT_BUILD_COMMIT"] = commit
        value["health_seconds"] = 180
        selected[name] = value
    # Wait for original jobs to settle before replacing their process owner.
    query = "import sqlite3,json;d=sqlite3.connect('file:/data/workspaces/worker-jobs.sqlite3?mode=ro',uri=True,timeout=.1);print(json.dumps(d.execute(\"SELECT id FROM jobs WHERE status='running'\").fetchall()))"
    due = time.monotonic() + 180
    while True:
        running = json.loads(
            release.run(["docker", "exec", "doxagent-v2-codex-worker-1", "python", "-c", query])
        )
        if not running:
            break
        if time.monotonic() >= due:
            raise RuntimeError("Retain built image; original running jobs have not drained")
        time.sleep(5)
    manifest = dict(
        commit=commit,
        compose_command=state["compose_base_command"],
        components=selected,
    )
    result = release.deploy(root, state, manifest)
    containers = {
        c["Config"]["Labels"].get("com.docker.compose.service"): c
        for c in release.inspect_containers()
    }
    files = json.loads((repo / "eval/runtime_p0_repair_20261028/source_files.json").read_text())
    expected = {
        f: hashlib.sha256(
            subprocess.check_output(["git", "-C", str(repo), "show", commit + ":" + f])
        ).hexdigest()
        for f in files
    }
    query = (
        "import hashlib,json,pathlib;files="
        + repr(files)
        + ";print(json.dumps({f:hashlib.sha256((pathlib.Path('/app')/f).read_bytes()).hexdigest() for f in files}))"
    )
    verified = {}
    for name in selected:
        container = containers[name]
        assert (
            json.loads(release.run(["docker", "exec", container["Id"], "python", "-c", query]))
            == expected
        )
        verified[name] = dict(
            container=container["Id"], image=container["Image"], source_files_verified=len(files)
        )
    receipt = dict(
        commit=commit,
        components=verified,
        unrelated_services_unchanged={
            n: containers.get(n, {}).get("Id") == identity
            for n, identity in before.items()
            if n not in selected
        },
        release=result,
    )
    release.atomic(root / ("runtime-p0-worker-" + commit[:12] + ".json"), receipt)
    print(json.dumps(receipt), flush=True)
