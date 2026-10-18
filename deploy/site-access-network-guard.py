#!/usr/bin/env python3
"""Rebind only Site Access when its native Supervisor network owner changes."""

import argparse
import json
import os
import subprocess
from pathlib import Path

SITE = "doxagent-v2-v2-site-access-1"
SUPERVISOR = "doxagent-v2-v2-chrome-supervisor-1"
REPO = Path("/home/ubuntu/doxagent")


def run(args, **kwargs):
    return subprocess.check_output(args, text=True, timeout=180, **kwargs).strip()


def inspect_binding(items, readlink=os.readlink):
    site, supervisor = items
    if not all(v["State"].get("Running") and v["State"].get("Pid") for v in items):
        return {"state": "DEFERRED", "reason": "container_not_running"}
    if supervisor["State"].get("Health", {}).get("Status") != "healthy":
        return {"state": "DEFERRED", "reason": "supervisor_not_healthy"}
    actual = readlink(f"/proc/{site['State']['Pid']}/ns/net")
    expected = readlink(f"/proc/{supervisor['State']['Pid']}/ns/net")
    bound = site["HostConfig"]["NetworkMode"] == "container:" + supervisor["Id"]
    return {
        "state": "READY" if bound and actual == expected else "MISMATCH",
        "site_namespace": actual,
        "supervisor_namespace": expected,
        "bound_current_supervisor": bound,
    }


def main():
    import fcntl

    parser = argparse.ArgumentParser()
    parser.add_argument("--repair", action="store_true")
    args = parser.parse_args()
    with open("/run/doxagent-site-access-deploy.lock", "a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({"state": "DEFERRED", "reason": "deployment_in_progress"}))
            return
        items = json.loads(run(["docker", "inspect", SITE, SUPERVISOR]))
        result = inspect_binding(items)
        if result["state"] == "MISMATCH" and args.repair:
            # Freeze the exact running image versions, not a potentially stale env tag.
            env = dict(
                os.environ,
                DOXAGENT_SITE_ACCESS_IMAGE=items[0]["Image"],
                DOXAGENT_CHROME_SUPERVISOR_IMAGE=items[1]["Image"],
            )
            command = [
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
                command.extend(["-f", str(REPO / name)])
            run(["docker", "stop", "--time", "-1", SITE])
            run(
                command
                + ["up", "-d", "--no-deps", "--no-build", "--force-recreate", "v2-site-access"],
                env=env,
            )
            after = json.loads(run(["docker", "inspect", SITE, SUPERVISOR]))
            if after[1]["Id"] != items[1]["Id"] or after[0]["Image"] != items[0]["Image"]:
                raise RuntimeError("deployment changed during network repair")
            result = inspect_binding(after)
            result["repaired"] = result["state"] == "READY"
        print(json.dumps(result))


if __name__ == "__main__":
    main()
