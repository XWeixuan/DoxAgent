"""Reconcile deployment receipt after Docker reordered equivalent mount entries."""

import json
import subprocess
from pathlib import Path

root = Path("/home/ubuntu/service-busy-repair-20261013")
path = root / "release.json"
receipt = json.loads(path.read_text())


def inspect(name):
    return json.loads(subprocess.check_output(["docker", "inspect", name]))[0]


def brief(value):
    return {
        "id": value["Id"],
        "image": value["Image"],
        "tag": value["Config"]["Image"],
        "started": value["State"]["StartedAt"],
    }


expected = {
    "/site-data": ("/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data", True),
    "/run/doxagent-resources": ("/run/doxagent-resources", False),
    "/run/doxagent-chrome": ("/var/lib/docker/volumes/doxagent-v2_v2-chrome-control/_data", True),
    "/run/secrets/site_access_worker_token": (
        "/home/ubuntu/doxagent/deploy/secrets/site_access_worker_token",
        False,
    ),
    "/run/secrets/site_access_admin_token": (
        "/home/ubuntu/doxagent/deploy/secrets/site_access_admin_token",
        False,
    ),
}
site = inspect("doxagent-v2-v2-site-access-1")
assert {m["Destination"]: (m["Source"], m["RW"]) for m in site["Mounts"]} == expected
assert site["Config"]["Image"] == "doxagent-site-access:lifecycle-20261013-r1"
receipt["services"]["v2-site-access"]["after"] = brief(site)
receipt["mount_check_note"] = (
    "Site mount list order changed; destination/source/read-write mapping unchanged. "
    "Deployment script comparison normalized."
)
receipt["untouched_after"] = {name: brief(inspect(name)) for name in receipt["untouched_before"]}
assert receipt["untouched_before"] == receipt["untouched_after"]
path.write_text(json.dumps(receipt, indent=2))
path.chmod(0o600)
print(
    json.dumps(
        {
            "services": {key: value["after"] for key, value in receipt["services"].items()},
            "unrelated_containers_unchanged": True,
        }
    )
)
