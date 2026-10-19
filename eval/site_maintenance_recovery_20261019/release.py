"""Coordinated immutable Site/Supervisor repair; keep every other production image."""

import argparse
import fcntl
import json
import os
import sqlite3
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

REPO = Path("/home/ubuntu/doxagent")
ROOT = Path("/var/lib/doxagent/site-maintenance-recovery-20261019")
SITE = "doxagent-v2-v2-site-access-1"
SUPERVISOR = "doxagent-v2-v2-chrome-supervisor-1"
CLIENTS = ["doxagent-v2-v2-message-bus-1", "doxagent-v2-v2-content-enrichment-1"]


def run(command, **kwargs):
    return subprocess.check_output(command, cwd=REPO, text=True, timeout=300, **kwargs).strip()


def inventory():
    ids = run(
        ["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project=doxagent-v2"]
    ).split()
    return {
        v["Name"].lstrip("/"): {"id": v["Id"], "image": v["Image"]}
        for v in json.loads(run(["docker", "inspect", *ids]))
    }


def save(name, value):
    p = ROOT / name
    p.write_text(json.dumps(value, indent=2))
    p.chmod(0o600)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--site-only", action="store_true")
    parser.add_argument("--replace-test-scenes", action="store_true")
    args = parser.parse_args()
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    commit = run(["git", "rev-parse", "HEAD"])
    assert not run(["git", "status", "--porcelain", "--untracked-files=no"])
    items = json.loads(run(["docker", "inspect", SITE, SUPERVISOR]))
    before = inventory()
    save("before-site-refinement.json" if args.site_only else "before.json", before)
    images = {}
    for kind, item in zip(("site-access", "chrome-supervisor"), items, strict=True):
        if args.site_only and kind == "chrome-supervisor":
            images[kind] = {
                "tag": item["Config"]["Image"],
                "image": item["Image"],
                "preserved": True,
            }
            continue
        base = item["Image"]
        base_info = json.loads(run(["docker", "image", "inspect", base]))[0]
        base_tag = f"doxagent-maintenance-base:{kind}-{base.split(':')[1][:12]}"
        run(["docker", "tag", base, base_tag])
        assert json.loads(run(["docker", "image", "inspect", base_tag]))[0]["Id"] == base
        base_commit = base_info["Config"]["Labels"]["org.opencontainers.image.revision"]
        tag = f"doxagent-{kind}:maintenance-{commit[:8]}"
        archive = subprocess.Popen(
            [
                "git",
                "archive",
                commit,
                "src/doxagent/site_strategy",
                "src/doxagent/message_bus_v2/yahoo_sources.py",
                "src/doxagent/message_bus_v2/reuters_sources.py",
                "src/doxagent/content_enrichment/quality.py",
                "deploy/Dockerfile.site-maintenance-repair",
            ],
            cwd=REPO,
            stdout=subprocess.PIPE,
        )
        subprocess.run(
            [
                "docker",
                "build",
                "-f",
                "deploy/Dockerfile.site-maintenance-repair",
                "--build-arg",
                "APPROVED_BASE=" + base_tag,
                "--build-arg",
                "BASE_COMMIT=" + base_commit,
                "--build-arg",
                "REPAIR_COMMIT=" + commit,
                "-t",
                tag,
                "-",
            ],
            stdin=archive.stdout,
            check=True,
            timeout=300,
        )
        assert archive.wait() == 0
        image = json.loads(run(["docker", "image", "inspect", tag]))[0]
        prefix = base_info["RootFS"]["Layers"]
        assert image["RootFS"]["Layers"][: len(prefix)] == prefix
        images[kind] = {
            "tag": tag,
            "image": image["Id"],
            "base": base,
            "base_commit": base_commit,
            "dependency_layers_preserved": True,
        }
    save(
        "build-site-refinement.json" if args.site_only else "build.json",
        {"repair_commit": commit, "images": images},
    )
    compose = [
        "docker",
        "compose",
        "-p",
        "doxagent-v2",
        "--project-directory",
        str(REPO),
        "--env-file",
        str(REPO / ".env.v2"),
    ]
    for name in (
        "docker-compose.v2-production.yml",
        "deploy/docker-compose.server.yml",
        "deploy/docker-compose.initialization-repair.yml",
    ):
        compose.extend(["-f", str(REPO / name)])
    with open("/run/doxagent-site-access-deploy.lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        env_file = REPO / ".env.v2"
        original = env_file.read_text()
        (ROOT / "env.before").write_text(original)
        (ROOT / "env.before").chmod(0o600)
        values = {
            "DOXAGENT_SITE_ACCESS_IMAGE": images["site-access"]["tag"],
            "DOXAGENT_CHROME_SUPERVISOR_IMAGE": images["chrome-supervisor"]["tag"],
        }
        lines = []
        for line in original.splitlines():
            key = line.split("=", 1)[0]
            lines.append(key + "=" + values.pop(key) if key in values else line)
        lines += [k + "=" + v for k, v in values.items()]
        stage = ROOT / "env.next"
        stage.write_text("\n".join(lines) + "\n")
        stage.chmod(env_file.stat().st_mode & 0o777)
        os.chown(stage, env_file.stat().st_uid, env_file.stat().st_gid)
        os.replace(stage, env_file)
        run(compose + ["config", "--quiet"])
        if args.site_only:
            run(["docker", "stop", "--time", "-1", SITE])
            run(
                compose
                + ["up", "-d", "--no-deps", "--no-build", "--force-recreate", "v2-site-access"]
            )
            for _ in range(120):
                state = json.loads(run(["docker", "inspect", SITE]))[0]["State"]
                if state.get("Health", {}).get("Status") == "healthy":
                    break
                time.sleep(1)
            else:
                raise RuntimeError("site did not become ready")
            after = inventory()
            assert all(after[k] == v for k, v in before.items() if k != SITE)
            save("after-site-refinement.json", after)
            print(json.dumps({"commit": commit, "images": images, "supervisor_preserved": True}))
            return
        run(["docker", "stop", "--time", "120", *CLIENTS])
        run(["docker", "stop", "--time", "-1", SITE])
        volume = next(m["Source"] for m in items[0]["Mounts"] if m["Destination"] == "/site-data")
        source = sqlite3.connect(str(Path(volume) / "registry/site_strategy.sqlite3"))
        tasks = [
            json.loads(v[0])
            for v in source.execute(
                "select data_json from challenge_tasks where state<>'RESOLVED' and superseded_by is null"
            )
        ]
        save("tasks.before.json", tasks)
        if args.replace_test_scenes:
            # Only pages created by this task's own acceptance operations, never
            # an independently created/manual login scene, may be discarded.
            for task in tasks:
                if task["state"] == "TARGET_LOST":
                    continue
                op = source.execute(
                    "select data_json from maintenance_operations where operation_id=?",
                    (task["operation_id"],),
                ).fetchone()
                assert op and json.loads(op[0])["idempotency_key"].startswith(
                    "maintenance-acceptance:"
                ), "unexpected human scene"
                task["state"] = "TARGET_LOST"
                task["reason"] = "acceptance_scene_discarded_for_viewer_upgrade"
                source.execute(
                    "update challenge_tasks set state=?,data_json=?,updated_at=? where task_id=?",
                    (
                        "TARGET_LOST",
                        json.dumps(task),
                        datetime.now(UTC).isoformat(),
                        task["task_id"],
                    ),
                )
                raw = source.execute(
                    "select data_json from owned_browser_targets where allocation_id=?",
                    (task["allocation_id"],),
                ).fetchone()
                if raw:
                    target = json.loads(raw[0])
                    target["state"] = "LOST"
                    source.execute(
                        "update owned_browser_targets set state=?,data_json=?,updated_at=? where allocation_id=?",
                        (
                            "LOST",
                            json.dumps(target),
                            datetime.now(UTC).isoformat(),
                            task["allocation_id"],
                        ),
                    )
            source.commit()
        assert all(t["state"] == "TARGET_LOST" for t in tasks), (
            "unexpected new human scene; keep supervisor"
        )
        backup = sqlite3.connect(str(ROOT / "registry.before.sqlite3"))
        source.backup(backup)
        backup.close()
        source.close()
        run(["docker", "stop", "--time", "-1", SUPERVISOR])
        run(
            compose
            + ["up", "-d", "--no-deps", "--no-build", "--force-recreate", "v2-chrome-supervisor"]
        )
        for _ in range(90):
            state = json.loads(run(["docker", "inspect", SUPERVISOR]))[0]["State"]
            if state.get("Health", {}).get("Status") == "healthy":
                break
            time.sleep(1)
        else:
            raise RuntimeError("supervisor did not become healthy")
        run(compose + ["up", "-d", "--no-deps", "--no-build", "--force-recreate", "v2-site-access"])
        for _ in range(120):
            state = json.loads(run(["docker", "inspect", SITE]))[0]["State"]
            if state.get("Health", {}).get("Status") == "healthy":
                break
            time.sleep(1)
        else:
            raise RuntimeError("site did not become ready")
        run(["docker", "start", *CLIENTS])
    run(["sh", "deploy/install-site-access-network-guard.sh"])
    after = inventory()
    other = {k: v for k, v in before.items() if k not in {SITE, SUPERVISOR}}
    assert all(after[k] == v for k, v in other.items())
    save("after.json", after)
    print(json.dumps({"commit": commit, "images": images, "other_container_changes": []}))


if __name__ == "__main__":
    main()
