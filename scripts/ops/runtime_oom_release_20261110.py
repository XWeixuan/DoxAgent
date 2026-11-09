"""Deploy the reviewed Runtime incident patch onto the actual production images.

Run as root on the Singapore host; checkout operations remain owned by ubuntu.
No model probes, case replays, full-history scans or synchronous image GC.
"""

import copy
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

sys.path.insert(0, "/home/ubuntu/doxagent/scripts/ops")
import storage_release as release


def command(*args):
    return subprocess.check_output(args, text=True).strip()


def file_in_container(container, path):
    raw = subprocess.check_output(["docker", "cp", container + ":/app/" + path, "-"])
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        return archive.extractfile(archive.getmembers()[0]).read()


def main():
    commit = sys.argv[1]
    os.umask(0o077)
    repo = Path("/home/ubuntu/doxagent")
    root = Path("/var/lib/doxagent/releases")
    audit = root / ("runtime-oom-repair-" + commit[:12])
    audit.mkdir(mode=0o700, exist_ok=True)
    baseline = json.loads(Path(sys.argv[2]).read_text())

    def git(*args):
        return subprocess.check_output(["sudo", "-u", "ubuntu", "git", "-C", str(repo), *args])

    git("fetch", "origin", "main")
    assert git("rev-parse", "origin/main").decode().strip() == commit
    paths = json.loads(git("show", commit + ":scripts/ops/runtime_oom_repair_files_20261110.json"))
    assert paths and all("prompt" not in p for p in paths)
    with release.lock(root):
        state = json.loads((root / "state.json").read_bytes())
        conf = json.loads((root / "compose.current.json").read_bytes())
        for name in ["state.json", "compose.current.json"]:
            shutil.copy2(root / name, audit / (name + ".before.private"))
        infos = {
            role: json.loads(command("docker", "inspect", "doxagent-v2-" + role + "-1"))[0]
            for role in ["codex-worker", "v2-scheduler"]
        }
        for role, info in infos.items():
            assert not info["State"]["Running"], role + " must remain stopped before build"
            assert info["Image"] == state["components"][role]["current"]["image"]
        labels = infos["v2-scheduler"]["Config"]["Labels"]
        base = [
            "docker",
            "compose",
            "--env-file",
            str(repo / ".env.v2"),
            "-p",
            labels["com.docker.compose.project"],
        ]
        for path in labels["com.docker.compose.project.config_files"].split(","):
            if path != str(root / "compose.current.json"):
                base += ["-f", path]
        guardian = json.loads(
            command("docker", "inspect", "doxagent-v2-initialization-guardian-1")
        )[0]
        for item in guardian["Config"]["Env"]:
            key, _, value = item.partition("=")
            if key.startswith("DOXAGENT_INITIALIZATION_REPAIR_"):
                os.environ[key] = value
        context = audit / "build"
        context.mkdir(exist_ok=True)
        selected = {
            "codex-worker": [p for p in paths if "/codex_worker/" in p or "/codex_runtime/" in p],
            "v2-scheduler": [
                p for p in paths if "/codex_worker/" not in p or p.endswith("/schema.py")
            ],
        }
        hashes = {}
        for path in paths:
            raw = git("show", commit + ":" + path)
            target = context / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
            hashes[path] = hashlib.sha256(raw).hexdigest()
            # Only files actually replaced require parity. Preserve all other image overlays.
            for role in infos:
                if path not in selected[role]:
                    continue
                old = subprocess.run(
                    ["sudo", "-u", "ubuntu", "git", "-C", str(repo), "show", "c34712ae:" + path],
                    capture_output=True,
                )
                actual = file_in_container("doxagent-v2-" + role + "-1", path)
                expected = baseline.get(role, {}).get(path) or (
                    hashlib.sha256(old.stdout).hexdigest() if old.returncode == 0 else None
                )
                assert hashlib.sha256(actual).hexdigest() == expected, (
                    role,
                    path,
                    "unreviewed production drift",
                )
        receipt = dict(commit=commit, files=hashes, bases={}, images={})
        manifest = dict(compose_command=base, commit=commit, components={})
        for role, info in infos.items():
            source = info["Image"]
            receipt["bases"][role] = source
            base_tag = "doxagent-v2:oom-base-" + role + "-" + source[7:19]
            subprocess.run(["docker", "tag", source, base_tag], check=True)
            lines = ["FROM " + base_tag, "USER root"] + [
                "COPY " + p + " /app/" + p for p in selected[role]
            ]
            lines += [
                'LABEL com.doxagent.runtime-oom-repair.commit="' + commit + '"',
                "USER " + (info["Config"].get("User") or "root"),
            ]
            dockerfile = context / ("Dockerfile." + role)
            dockerfile.write_text("\n".join(lines) + "\n")
            tag = "doxagent-v2:oom-repair-" + commit[:12] + "-" + role
            subprocess.run(
                ["docker", "build", "-t", tag, "-f", str(dockerfile), str(context)], check=True
            )
            image = command("docker", "image", "inspect", "--format", "{{.Id}}", tag)
            receipt["images"][role] = image
            if image not in state.setdefault("owned_images", []):
                state["owned_images"].append(image)
            candidate = copy.deepcopy(conf["services"][role])
            candidate["image"] = image
            candidate.setdefault("environment", {})["DOXAGENT_RUNTIME_REPAIR_COMMIT"] = commit
            candidate["schema"] = state["components"][role]["current"].get("schema")
            check = "import hashlib;" + "".join(
                "assert hashlib.sha256(open("
                + repr("/app/" + p)
                + ',"rb").read()).hexdigest()=='
                + repr(h)
                + ";"
                for p, h in hashes.items()
                if p in selected[role]
            )
            candidate["checks"] = [
                ["docker", "exec", "doxagent-v2-" + role + "-1", "python", "-c", check]
            ]
            candidate["health_seconds"] = 180
            manifest["components"][role] = candidate
        release.atomic(root / "state.json", state)
        release.atomic(audit / "build.json", receipt)
        release.atomic(audit / "manifest.private.json", manifest)
        # Reconciliation is registered, but old-image deletion/pruning must not
        # compete with recovery and the requested observation window.
        release.reconcile = lambda *_: dict(errors=[], deferred="observe before image cleanup")
        receipt["released_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        release.deploy(root, state, manifest)
        state["cleanup_pending"] = True
        release.atomic(root / "state.json", state)
        compose = [*base, "-f", str(root / "compose.current.json")]
        roles = [
            "v2-maintenance",
            "v2-o4",
            "v2-cdecr-executor",
            "v2-initialization",
            "initialization-guardian",
        ]
        configuration = json.loads(command(*compose, "config", "--format", "json"))
        for role in roles:
            policy = configuration["services"][role].get("restart", "unless-stopped")
            subprocess.run(
                ["docker", "update", "--restart", policy, "doxagent-v2-" + role + "-1"],
                check=True,
                stdout=subprocess.DEVNULL,
            )
        subprocess.run([*compose, "up", "-d", "--no-deps", "--no-build", *roles], check=True)
        receipt["completed_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        receipt["restored_roles"] = roles
        release.atomic(audit / "release.json", receipt)
        print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    main()
