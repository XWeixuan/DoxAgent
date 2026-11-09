"""Publish the shared-writer follow-up onto the accepted Scheduler image."""

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
commit = sys.argv[1]
repo = "/home/ubuntu/doxagent"
root = Path("/var/lib/doxagent/releases")


def git(*args):
    return subprocess.check_output(["sudo", "-u", "ubuntu", "git", "-C", repo, *args])


git("fetch", "origin", "main")
assert git("rev-parse", "origin/main").decode().strip() == commit
path = "src/doxagent/persistent_runtime_v2/repository.py"
with release.lock(root):
    state = json.loads((root / "state.json").read_text())
    conf = json.loads((root / "compose.current.json").read_text())
    audit = root / ("runtime-oom-writer-" + commit[:12])
    audit.mkdir(mode=0o700, exist_ok=True)
    for name in ["state.json", "compose.current.json"]:
        (audit / (name + ".before.private")).write_bytes((root / name).read_bytes())
    info = json.loads(subprocess.check_output(["docker", "inspect", "doxagent-v2-v2-scheduler-1"]))[
        0
    ]
    assert info["Image"] == state["components"]["v2-scheduler"]["current"]["image"]
    old = git("show", "f8bf7654:" + path)
    with tarfile.open(
        fileobj=io.BytesIO(
            subprocess.check_output(
                ["docker", "cp", "doxagent-v2-v2-scheduler-1:/app/" + path, "-"]
            )
        )
    ) as archive:
        assert archive.extractfile(archive.getmembers()[0]).read() == old
    source = git("show", commit + ":" + path)
    (audit / "repository.py").write_bytes(source)
    sha = hashlib.sha256(source).hexdigest()
    base = "doxagent-v2:writer-base-" + info["Image"][7:19]
    subprocess.run(["docker", "tag", info["Image"], base], check=True)
    (audit / "Dockerfile").write_text(
        "FROM "
        + base
        + "\nUSER root\nCOPY repository.py /app/"
        + path
        + "\nUSER "
        + (info["Config"].get("User") or "root")
        + "\n"
    )
    tag = "doxagent-v2:oom-writer-" + commit[:12]
    subprocess.run(["docker", "build", "-t", tag, str(audit)], check=True)
    image = subprocess.check_output(
        ["docker", "image", "inspect", "--format", "{{.Id}}", tag], text=True
    ).strip()
    state.setdefault("owned_images", []).append(image)
    release.atomic(root / "state.json", state)
    candidate = copy.deepcopy(conf["services"]["v2-scheduler"])
    candidate["image"] = image
    candidate.setdefault("environment", {})["DOXAGENT_RUNTIME_REPAIR_COMMIT"] = commit
    candidate["checks"] = [
        [
            "docker",
            "exec",
            "doxagent-v2-v2-scheduler-1",
            "python",
            "-c",
            "import hashlib;assert hashlib.sha256(open("
            + repr("/app/" + path)
            + ',"rb").read()).hexdigest()=='
            + repr(sha),
        ]
    ]
    guardian = json.loads(
        subprocess.check_output(["docker", "inspect", "doxagent-v2-initialization-guardian-1"])
    )[0]
    for item in guardian["Config"]["Env"]:
        key, _, value = item.partition("=")
        if key.startswith("DOXAGENT_INITIALIZATION_REPAIR_"):
            os.environ[key] = value
    release.reconcile = lambda *_: dict(errors=[], deferred="observation before image cleanup")
    subprocess.run(["docker", "stop", "-t", "15", "doxagent-v2-v2-scheduler-1"], check=True)
    release.deploy(
        root,
        state,
        dict(
            commit=commit,
            compose_command=state["compose_base_command"],
            components={"v2-scheduler": candidate},
        ),
    )
    receipt = dict(commit=commit, image=image, previous=info["Image"], sha256=sha)
    release.atomic(audit / "release.json", receipt)
    print(json.dumps(receipt))
