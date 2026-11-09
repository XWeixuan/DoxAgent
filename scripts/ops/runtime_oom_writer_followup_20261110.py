"""Publish the shared-writer follow-up onto the accepted Scheduler image."""

import argparse
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

sys.path.insert(0, "/home/ubuntu/doxagent/scripts/ops")
import storage_release as release

os.umask(0o077)
parser = argparse.ArgumentParser()
parser.add_argument('commit')
parser.add_argument('baseline', nargs='?', default='f8bf7654')
parser.add_argument('paths', nargs='*')
parser.add_argument('--role', choices=['v2-scheduler', 'codex-worker'], default='v2-scheduler')
args = parser.parse_args()
commit = args.commit
role = args.role
container = 'doxagent-v2-' + role + '-1'
repo = "/home/ubuntu/doxagent"
root = Path("/var/lib/doxagent/releases")


def git(*args):
    return subprocess.check_output(["sudo", "-u", "ubuntu", "git", "-C", repo, *args])


git("fetch", "origin", "main")
assert git("rev-parse", "origin/main").decode().strip() == commit
baseline = args.baseline
paths = args.paths or ["src/doxagent/persistent_runtime_v2/repository.py"]
assert all(p.startswith("src/doxagent/") and ".." not in p for p in paths)
with release.lock(root):
    state = json.loads((root / "state.json").read_text())
    conf = json.loads((root / "compose.current.json").read_text())
    audit = root / ("runtime-oom-" + role + "-" + commit[:12])
    audit.mkdir(mode=0o700, exist_ok=True)
    for name in ["state.json", "compose.current.json"]:
        (audit / (name + ".before.private")).write_bytes((root / name).read_bytes())
    info = json.loads(subprocess.check_output(["docker", "inspect", container]))[
        0
    ]
    assert info["Image"] == state["components"][role]["current"]["image"]
    context = audit / "build"
    context.mkdir(exist_ok=True)
    hashes = {}
    for path in paths:
        old = git("show", baseline + ":" + path)
        with tarfile.open(
            fileobj=io.BytesIO(
                subprocess.check_output(
                    ["docker", "cp", container + ":/app/" + path, "-"]
                )
            )
        ) as archive:
            assert archive.extractfile(archive.getmembers()[0]).read() == old, path
        source = git("show", commit + ":" + path)
        target = context / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source)
        hashes[path] = hashlib.sha256(source).hexdigest()
    base = "doxagent-v2:writer-base-" + info["Image"][7:19]
    subprocess.run(["docker", "tag", info["Image"], base], check=True)
    (context / "Dockerfile").write_text(
        "FROM "
        + base
        + "\nUSER root\n"
        + "\n".join("COPY " + p + " /app/" + p for p in paths)
        + "\nUSER "
        + (info["Config"].get("User") or "root")
        + "\n"
    )
    tag = "doxagent-v2:oom-" + role + "-" + commit[:12]
    subprocess.run(["docker", "build", "-t", tag, str(context)], check=True)
    image = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag], text=True
    ).strip()
    state.setdefault("owned_images", []).append(image)
    release.atomic(root / "state.json", state)
    candidate = copy.deepcopy(conf["services"][role])
    candidate["image"] = image
    candidate.setdefault("environment", {})["DOXAGENT_RUNTIME_REPAIR_COMMIT"] = commit
    check = "import hashlib;" + "".join(
        "assert hashlib.sha256(open("
        + repr("/app/" + p)
        + ",'rb').read()).hexdigest()=="
        + repr(h)
        + ";"
        for p, h in hashes.items()
    )
    candidate["checks"] = [["docker", "exec", container, "python", "-c", check]]
    guardian = json.loads(
        subprocess.check_output(["docker", "inspect", "doxagent-v2-initialization-guardian-1"])
    )[0]
    for item in guardian["Config"]["Env"]:
        key, _, value = item.partition("=")
        if key.startswith("DOXAGENT_INITIALIZATION_REPAIR_"):
            os.environ[key] = value
    release.reconcile = lambda *_: dict(errors=[], deferred="observation before image cleanup")
    subprocess.run(["docker", "stop", "-t", "15", container], check=True)
    release.deploy(
        root,
        state,
        dict(
            commit=commit,
            compose_command=state["compose_base_command"],
            components={role: candidate},
        ),
    )
    receipt = dict(commit=commit, role=role, image=image, previous=info["Image"], files=hashes)
    release.atomic(audit / "release.json", receipt)
    print(json.dumps(receipt))
