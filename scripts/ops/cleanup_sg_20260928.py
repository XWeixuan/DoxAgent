"""One-incident allowlisted cleanup; defaults to a read-only plan.

Run on the audited Singapore host as root, with --apply to execute.
Never prune volumes, stop services, or force removal of referenced images.
"""

import argparse
import glob
import http.client
import json
import os
import shutil
import socket
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path

DATA = Path("/var/lib/docker/volumes/doxagent-v2_v2-data/_data")
OLD_CONTAINERS = {
    "doxagent-v2-v2-migrate-1",
    "doxagent-barrons-cdp-attach-control",
    "doxagent-barrons-playwright-chrome-control",
    "doxagent-barrons-chrome-control",
}
IMAGE_PREFIXES = """
c3164ba015b9 0fa5a0d26dc9 aa56cf474fe5 fcd0afa82824 fa89f2c516fe
a51d283d1885 0f35a5cf4db7 3448bb6a2e05 76e1a0e348c2 0968fc6535d0
bc1c9d2f5fba 1d0302752974 d26c2f05da0a a0fb82d1455d 59a8667d51c7
2c35bd3919fc 185123804dfc e945d9322647 4602401760a4 8dee3dcab4a9
feb58a86a2a1 b92ea7a33466 b18c4f698734 ea26c00129d7 273e9dcc8cfc
66d4f26fb7d9 ffc029f74198 862b20aab9b5 145108832817 ab541c68601b
5b8656318cc9 3d073d9f44a5 c46c6abfbd0d 6b9ce7c70a5f 0b332cd940c3
886930193da1 81466dd366a1 a56ca63de848 b7ac1072b232 463eaf607268
333a8eff67ba 94e9e444bcba
""".split()
OLD_BACKUPS = """
20260909T090259908951Z 20260909T112927992300Z 20260909T112936438024Z
20260909T170903228424Z 20260909T170933007146Z 20260909T172458225952Z
20260910T053607491743Z 20260910T053618682079Z 20260910T060135889148Z
20260911T105445797465Z 20260911T105756476002Z 20260911T140324426522Z
20260913T151451127807Z 20260914T100440657364Z
20260914T141029911261Z 20260914T141029911261Z-resume-runtime
""".split()
GUARDIAN = "doxagent-v2-initialization-guardian-1"
AGENT_TAG = "doxagent-initialization-repair-agent:server"
TEMPLATE = "doxagent-v2-initialization-repair-template"


def run(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT)


class DockerHTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect("/var/run/docker.sock")


def docker_df():
    client = DockerHTTP("localhost")
    client.request("GET", "/system/df")
    response = client.getresponse()
    if response.status != 200:
        raise RuntimeError("Docker inventory unavailable")
    return json.loads(response.read())


def containers():
    ids = run(["docker", "ps", "-aq"]).split()
    return json.loads(run(["docker", "inspect", *ids]))


def protected_images(current):
    protected = {c["Image"] for c in current if c["Name"].lstrip("/") not in OLD_CONTAINERS}
    guardian = next(c for c in current if c["Name"].lstrip("/") == GUARDIAN)
    env = dict(e.split("=", 1) for e in guardian["Config"]["Env"])
    if env.get("DOXAGENT_INITIALIZATION_REPAIR_AGENT_IMAGE") != AGENT_TAG:
        raise RuntimeError("Guardian agent configuration changed; re-audit required")
    if env.get("DOXAGENT_INITIALIZATION_REPAIR_PRODUCTION_CONTAINER") != TEMPLATE:
        raise RuntimeError("Guardian template configuration changed; re-audit required")
    agent = json.loads(run(["docker", "image", "inspect", AGENT_TAG]))[0]
    protected.add(agent["Id"])
    return protected


def safe_target(path):
    if path.is_symlink() or not path.exists():
        raise RuntimeError("Missing/symlink cleanup target: " + str(path))
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(DATA.resolve(strict=True)) or resolved == DATA.resolve():
        raise RuntimeError("Cleanup target escaped data volume")
    if resolved != path:
        raise RuntimeError("Cleanup path contains a symlink")
    if path.is_dir():
        for base, dirs, files in os.walk(path):
            if any(Path(base, n).is_symlink() for n in dirs + files):
                raise RuntimeError("Nested symlink in cleanup target")
    return path


def backup_targets():
    candidates = [DATA / "backups" / n for n in OLD_BACKUPS]
    recovery = DATA / "backups/20260911T1050Z-o2-authoritative-recovery"
    candidates += [
        recovery / n
        for n in ["runtime.sqlite3", "initialization-control.sqlite3", "read-v2.sqlite3"]
    ]
    candidates += [
        DATA / "incident-backups/message-freshness-20260914",
        DATA / "bus/backups/bus.sqlite3.pre-a7889a34-20260923T175212Z.bak",
        DATA / "bus/backups/bus.sqlite3.pre-acquisition-20260923T180335233986Z.bak",
        DATA / "bus/bus.sqlite3.before-bc958c68",
    ]
    return [safe_target(p) for p in candidates if p.exists() or p.is_symlink()]


def check_backup_dependencies(targets, current):
    prefixes = tuple(str(p) for p in targets)
    for c in current:
        values = (c["Config"].get("Env") or []) + (c["Config"].get("Cmd") or [])
        if any(
            any(p in value or p.replace(str(DATA), "/data") in value for p in prefixes)
            for value in values
        ):
            raise RuntimeError("Container configuration references cleanup backup")
    for fd in glob.glob("/proc/[0-9]*/fd/*"):
        try:
            link = os.readlink(fd)
        except OSError:
            continue
        if any(link == p or link.startswith(p + "/") for p in prefixes):
            raise RuntimeError("Backup is currently open: " + link)
    for relative in [
        "research/research.sqlite3",
        "initialization/control.sqlite3",
        "bus/bus.sqlite3",
        "runtime/runtime.sqlite3",
    ]:
        with sqlite3.connect((DATA / relative).as_uri() + "?mode=ro", uri=True, timeout=0.2) as db:
            for table in ["v2_receipt_archive", "v2_source_pins"]:
                if db.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone():
                    if db.execute("SELECT 1 FROM " + table + " LIMIT 1").fetchone():
                        raise RuntimeError(
                            "Source archive/pin appeared; backup dependencies need re-audit"
                        )


def snapshot(current):
    disk = os.statvfs("/")
    return {
        "available_bytes": disk.f_bavail * disk.f_frsize,
        "used_bytes": (disk.f_blocks - disk.f_bfree) * disk.f_frsize,
        "containers": [
            {
                "name": c["Name"],
                "id": c["Id"],
                "image": c["Image"],
                "status": c["State"]["Status"],
                "mounts": [
                    {"source": m["Source"], "destination": m["Destination"]} for m in c["Mounts"]
                ],
            }
            for c in current
        ],
        "volumes": run(["docker", "volume", "ls", "-q"]).split(),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if socket.gethostname() != "VM-0-15-ubuntu" or os.geteuid() != 0:
        raise RuntimeError("Wrong host or privilege")
    current = containers()
    protected = protected_images(current)
    targets = backup_targets()
    check_backup_dependencies(targets, current)
    inventory = docker_df()
    if any(c.get("InUse") for c in inventory.get("BuildCache", [])):
        raise RuntimeError("Build cache is in use; wait for the active build")
    candidates = [
        i
        for i in inventory["Images"]
        if any(i["Id"].split(":")[-1].startswith(p) for p in IMAGE_PREFIXES)
    ]
    if any(i["Id"] in protected for i in candidates):
        raise RuntimeError("Image allowlist overlaps operational dependencies")
    planned = {
        "images": [{"id": i["Id"], "tags": i.get("RepoTags")} for i in candidates],
        "backup_targets": [str(p) for p in targets],
        "old_containers": sorted(OLD_CONTAINERS),
    }
    if not args.apply:
        print(
            json.dumps(
                {"mode": "plan", "protected_image_count": len(protected), **planned}, indent=2
            )
        )
        return
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    journal = Path("/var/lib/doxagent/ops") / ("cleanup-" + stamp)
    journal.mkdir(parents=True, exist_ok=False)
    before = snapshot(current)
    (journal / "before.json").write_text(json.dumps(before, indent=2))
    (journal / "plan.json").write_text(json.dumps(planned, indent=2))
    events = []

    def record(kind, target, detail):
        event = {"kind": kind, "target": target, "detail": detail}
        events.append(event)
        with (journal / "events.jsonl").open("a") as stream:
            stream.write(json.dumps(event) + "\n")
        print(kind, target, detail[-240:], flush=True)

    for c in current:
        if c["Name"].lstrip("/") in OLD_CONTAINERS:
            latest = json.loads(run(["docker", "inspect", c["Id"]]))[0]
            if latest["State"]["Status"] not in {"exited", "created"}:
                raise RuntimeError("Old container started; refusing deletion")
            record("container_removed", c["Name"], run(["docker", "rm", c["Id"]]))
    for i in candidates:
        fresh = containers()
        if i["Id"] in protected_images(fresh) or any(c["Image"] == i["Id"] for c in fresh):
            record("image_skipped", i["Id"], "new dependency")
            continue
        image = json.loads(run(["docker", "image", "inspect", i["Id"]]))[0]
        refs = image.get("RepoTags") or [i["Id"]]
        # Removing all audited tags avoids forcing a multi-tag image deletion.
        record("image_removed", i["Id"], run(["docker", "image", "rm", *refs]))
    record(
        "cache_pruned", "default builder", run(["docker", "buildx", "prune", "--all", "--force"])
    )
    check_backup_dependencies(targets, containers())
    for p in targets:
        safe_target(p)
        if p.is_dir():
            shutil.rmtree(p)
        else:
            p.unlink()
        record(
            "backup_removed", str(p), "deleted old recovery point; not recoverable on this server"
        )
    after_containers = containers()
    protected_images(after_containers)
    after = snapshot(after_containers)
    for c in before["containers"]:
        if c["status"] == "running":
            live = next((x for x in after["containers"] if x["id"] == c["id"]), None)
            if live is None or live["status"] != "running" or live["image"] != c["image"]:
                raise RuntimeError("Previously running service changed")
    if before["volumes"] != after["volumes"]:
        raise RuntimeError("Volume inventory changed; inspect before acceptance")
    (journal / "after.json").write_text(json.dumps(after, indent=2))
    print(
        json.dumps(
            {
                "journal": str(journal),
                "reclaimed_GiB": (after["available_bytes"] - before["available_bytes"]) / 2**30,
                "available_GiB": after["available_bytes"] / 2**30,
                "events": len(events),
                "running_services_unchanged": True,
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
