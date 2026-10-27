"""Canonical targeted release: preserve unrelated services and original work."""

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


def inside(container, code):
    process = subprocess.run(
        ["docker", "exec", "-i", container, "python", "-"],
        input=code,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(process.stdout)


def inventory():
    return {
        c["Config"]["Labels"].get("com.docker.compose.service"): c
        for c in release.inspect_containers()
        if c["Config"]["Labels"].get("com.docker.compose.project") == "doxagent-v2"
    }


with release.lock(root):
    assert release.run(["git", "-C", str(repo), "rev-parse", "HEAD"]) == commit
    audit = root / ("runtime-p0-" + commit[:12])
    audit.mkdir(exist_ok=True)
    before = inventory()
    release.atomic(
        audit / "before.json", {k: dict(id=v["Id"], image=v["Image"]) for k, v in before.items()}
    )
    build = release.build(repo, commit, root, ["backend", "web"])
    release.atomic(audit / "build.json", build)
    print(
        json.dumps(
            dict(
                phase="built",
                commit=commit,
                images={k: v["image"] for k, v in build["images"].items()},
            )
        ),
        flush=True,
    )
    worker = "doxagent-v2-codex-worker-1"
    code = "import sqlite3,json;d=sqlite3.connect('file:/data/workspaces/worker-jobs.sqlite3?mode=ro',uri=True);print(json.dumps(d.execute(\"SELECT id,status FROM jobs WHERE status='running'\").fetchall()))"
    running = inside(worker, code)
    # No active SDK turn is interrupted. Queued original work is durable and retained.
    due = time.monotonic() + 180
    while running:
        if time.monotonic() >= due:
            raise RuntimeError(
                "Running Worker tasks have not drained; retain built images and retry release"
            )
        time.sleep(5)
        running = inside(worker, code)
    bus = inventory()["v2-message-bus"]
    path = inside(
        bus["Id"],
        "import json;from doxagent.settings import DoxAgentSettings;print(json.dumps(str(DoxAgentSettings().message_bus_v2_sqlite_path)))",
    )
    migration = "from doxagent.ticker_initialization.configuration import CandidateConfiguration;import sys;CandidateConfiguration.migrate_install_schema(sys.argv[1]);print('configuration schema ready')"
    subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--volumes-from",
            bus["Id"],
            "--entrypoint",
            "python",
            build["images"]["backend"]["tag"],
            "-c",
            migration,
            path,
        ],
        check=True,
    )
    state = json.loads((root / "state.json").read_text())
    current = json.loads((root / "compose.current.json").read_text())
    names = ["codex-worker", "v2-projector", "v2-api", "v2-message-bus", "v2-scheduler", "v2-web"]
    components = {}
    for name in names:
        candidate = copy.deepcopy(current["services"].get(name, {}))
        candidate["image"] = build["images"]["web" if name == "v2-web" else "backend"]["tag"]
        candidate.setdefault("environment", {})["DOXAGENT_BUILD_COMMIT"] = commit
        candidate["health_seconds"] = 180
        components[name] = candidate
    manifest = dict(
        commit=commit, compose_command=state["compose_base_command"], components=components
    )
    release.atomic(audit / "manifest.private.json", manifest)
    release.deploy(root, state, manifest)
    after = inventory()
    source_files = json.loads(
        (repo / "eval/runtime_p0_repair_20261028/source_files.json").read_text()
    )
    expected = {
        f: hashlib.sha256(
            subprocess.check_output(["git", "-C", str(repo), "show", commit + ":" + f])
        ).hexdigest()
        for f in source_files
    }
    code = (
        "import hashlib,json,pathlib;files="
        + repr(source_files)
        + ";print(json.dumps({f:hashlib.sha256((pathlib.Path('/app')/f).read_bytes()).hexdigest() for f in files}))"
    )
    verified = {}
    for name in names:
        container = after[name]
        env = dict(v.split("=", 1) for v in container["Config"]["Env"])
        assert env.get("DOXAGENT_BUILD_COMMIT") == commit, (name, "build revision")
        if name != "v2-web":
            actual = inside(container["Id"], code)
            assert actual == expected, (name, "source digest mismatch")
        verified[name] = dict(
            id=container["Id"],
            image=container["Image"],
            restart_count=container["RestartCount"],
            health=container["State"].get("Health", {}).get("Status"),
            source_files_verified=len(source_files) if name != "v2-web" else None,
        )
    unchanged = {
        n: after.get(n, {}).get("Id") == c["Id"] for n, c in before.items() if n not in names
    }
    receipt = dict(
        commit=commit,
        components=verified,
        unrelated_services_unchanged=unchanged,
        empty_running_worker_queue_before_release=True,
        audit_directory=str(audit),
    )
    release.atomic(audit / "receipt.json", receipt)
    print(json.dumps(receipt), flush=True)
