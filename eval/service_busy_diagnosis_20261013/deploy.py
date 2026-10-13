"""Narrow API/Site Access overlay using each container's exact Compose chain."""

import json
import subprocess
from pathlib import Path

ROOT = Path("/home/ubuntu/service-busy-repair-20261013")
SERVICES = {
    "v2-api": ("doxagent-v2:v21-production-20261012-r2", "doxagent-v2:service-busy-20261013-r1"),
    "v2-site-access": (
        "doxagent-site-access:source-recovery-20261012-r1",
        "doxagent-site-access:lifecycle-20261013-r1",
    ),
}


def run(args, timeout=120):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr[-2500:] or result.stdout[-2500:])
    return result.stdout


def inspect(name):
    return json.loads(run(["docker", "inspect", name]))[0]


def brief(value):
    return {
        "id": value["Id"],
        "image": value["Image"],
        "tag": value["Config"]["Image"],
        "started": value["State"]["StartedAt"],
    }


def main():
    assert ROOT.resolve() == ROOT and ROOT.is_dir()
    untouched = {}
    for name in run(["docker", "ps", "--format", "{{.Names}}"]).split():
        if name not in ["doxagent-v2-v2-api-1", "doxagent-v2-v2-site-access-1"]:
            untouched[name] = brief(inspect(name))
    receipt = {"untouched_before": untouched, "services": {}}
    receipt_path = ROOT / "release.json"

    def save():
        receipt_path.write_text(json.dumps(receipt, indent=2))
        receipt_path.chmod(0o600)

    configs = {}
    for service, (old_tag, new_tag) in SERVICES.items():
        name = "doxagent-v2-" + service + "-1"
        old = inspect(name)
        assert old["Config"]["Image"] == old_tag, "production baseline changed: " + service
        label = old["Config"]["Labels"]
        files = label["com.docker.compose.project.config_files"].split(",")
        assert all(Path(path).is_file() for path in files)
        overlay = ROOT / (service + ".yaml")
        overlay.write_text("services:\n  " + service + ":\n    image: " + new_tag + "\n")
        compose = [
            "docker",
            "compose",
            "-p",
            "doxagent-v2",
            "--env-file",
            "/home/ubuntu/doxagent/.env.v2",
        ]
        for file in files + [str(overlay)]:
            compose += ["-f", file]
        run(compose + ["config", "--quiet"])
        configs[service] = (compose, old)
        receipt["services"][service] = {
            "before": brief(old),
            "compose_files": files,
            "overlay": str(overlay),
        }
    save()
    for service, (_, new_tag) in SERVICES.items():
        kind = "api" if service == "v2-api" else "site"
        print("building " + service, flush=True)
        run(
            [
                "docker",
                "build",
                "-f",
                str(ROOT / "eval/service_busy_diagnosis_20261013" / ("Dockerfile." + kind)),
                "-t",
                new_tag,
                str(ROOT),
            ],
            timeout=180,
        )
    for service, (compose, old) in configs.items():
        print("recreating only " + service, flush=True)
        # No dependencies, migration command, DB replacement or Supervisor restart.
        run(compose + ["up", "-d", "--no-deps", "--no-build", "-t", "20", service], timeout=90)
        new = inspect("doxagent-v2-" + service + "-1")
        assert new["Config"]["Image"] == SERVICES[service][1]
        assert sorted(new["Mounts"], key=lambda m: m["Destination"]) == sorted(
            old["Mounts"], key=lambda m: m["Destination"]
        )
        assert set(new["Config"]["Env"]) == set(old["Config"]["Env"])
        assert new["HostConfig"]["Memory"] == old["HostConfig"]["Memory"]
        receipt["services"][service]["after"] = brief(new)
        save()
    receipt["untouched_after"] = {name: brief(inspect(name)) for name in untouched}
    assert receipt["untouched_before"] == receipt["untouched_after"], "unrelated container changed"
    save()
    print(
        json.dumps(
            {
                "deployed": {key: value["after"] for key, value in receipt["services"].items()},
                "unrelated_containers_unchanged": True,
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
