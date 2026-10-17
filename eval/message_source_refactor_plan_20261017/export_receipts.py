"""Export only non-secret release receipts. Registry backups and env stay on host."""

import json
import os
import pwd
import shutil
import statistics
import subprocess
import tarfile
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("/var/lib/doxagent/source-isolation-release-20261018")
SITE_ROOT = Path(
    "/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/source-isolation-release-20261018"
)
SITE = "doxagent-v2-v2-site-access-1"


def run(args, **kwargs):
    return subprocess.check_output(args, text=True, **kwargs).strip()


def main():
    result = {
        "at": datetime.now(UTC).isoformat(),
        "root_available_bytes": shutil.disk_usage("/").free,
    }
    names = run(
        ["docker", "ps", "-q", "--filter", "label=com.docker.compose.project=doxagent-v2"]
    ).split()
    current = json.loads(run(["docker", "inspect", *names]))
    result["containers"] = {
        v["Name"].lstrip("/"): {
            "id": v["Id"],
            "image": v["Image"],
            "tag": v["Config"]["Image"],
            "health": v["State"].get("Health", {}).get("Status"),
            "memory_limit": v["HostConfig"]["Memory"],
            "pids_limit": v["HostConfig"]["PidsLimit"],
        }
        for v in current
    }
    before = json.loads((ROOT / "before.json").read_text())["containers"]
    allowed = {
        "doxagent-v2-v2-" + n + "-1"
        for n in ["site-access", "chrome-supervisor", "message-bus", "content-enrichment"]
    }
    result["other_container_changes"] = [
        name
        for name, v in before.items()
        if name not in allowed and result["containers"].get(name, {}).get("id") != v["container"]
    ]
    result["source_maintenance_worker_running"] = any(
        "source-maintenance" in n for n in result["containers"]
    )
    result["source_provenance"] = {}
    for name in sorted(allowed):
        inspected = next(v for v in current if v["Name"].lstrip("/") == name)
        commit = inspected["Config"]["Labels"]["org.opencontainers.image.revision"]
        entries = subprocess.check_output(
            [
                "git",
                "-C",
                "/home/ubuntu/doxagent",
                "ls-tree",
                "-rz",
                "--full-tree",
                commit,
                "--",
                "src",
                "prompts",
                "dev_plan/workflow_v2/api_contract",
            ]
        )
        expected = {}
        for raw in entries.split(b"\0"):
            if not raw:
                continue
            metadata, path = raw.split(b"\t", 1)
            mode, kind, oid = metadata.decode().split()
            path = path.decode()
            if path.startswith("src/doxagent/dashboard_api/"):
                continue
            expected[path] = {"oid": oid, "mode": mode}
        script = r"""
import hashlib,json,sys,os
from pathlib import Path
expected=json.load(sys.stdin);mismatches=[]
for name,v in expected.items():
    path=Path('/app')/name
    if not path.exists() and not path.is_symlink():mismatches.append({'path':name,'reason':'missing'});continue
    if v['mode']=='120000':
        data=os.readlink(path).encode();h=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data)
    else:
        h=hashlib.sha1(b'blob '+str(path.stat().st_size).encode()+b'\0')
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
    if h.hexdigest()!=v['oid']:mismatches.append({'path':name,'reason':'blob_mismatch'})
print(json.dumps({'checked':len(expected),'mismatches':mismatches}))
"""
        # Use -c for code, stdin only for the hash manifest, never any credentials.
        checked = json.loads(
            run(
                ["docker", "exec", "-i", name, "/app/.venv/bin/python", "-c", script],
                input=json.dumps(expected),
            )
        )
        checked["commit"] = commit
        result["source_provenance"][name] = checked
    result["untracked_application_files"] = run(
        [
            "git",
            "-C",
            "/home/ubuntu/doxagent",
            "ls-files",
            "--others",
            "--exclude-standard",
            "--",
            "src",
            "prompts",
        ]
    ).splitlines()
    samples = []
    for _ in range(8):
        started = time.monotonic()
        inventory = json.loads(
            run(
                [
                    "sudo",
                    "-u",
                    "doxagent-desktop",
                    "sudo",
                    "-n",
                    "/usr/local/sbin/doxagent-site-login-admin",
                    "list",
                ]
            )
        )
        samples.append(time.monotonic() - started)
        assert inventory.get("status") == "READY"
    result["bridge_inventory_seconds"] = {
        "samples": samples,
        "median": statistics.median(samples),
        "p95_nearest_rank": sorted(samples)[-1],
    }
    groups = run(["id", "-nG", "doxagent-desktop"]).split()
    result["desktop_not_in_docker_group"] = "docker" not in groups
    secret = Path("/home/ubuntu/doxagent/deploy/secrets/site_access_admin_token")
    result["desktop_cannot_read_admin_token"] = (
        subprocess.run(["sudo", "-u", "doxagent-desktop", "test", "-r", str(secret)]).returncode
        != 0
    )
    result["desktop_cannot_write_docker_socket"] = (
        subprocess.run(
            ["sudo", "-u", "doxagent-desktop", "test", "-w", "/var/run/docker.sock"]
        ).returncode
        != 0
    )
    result["installed_bridge_mode"] = oct(
        Path("/usr/local/sbin/doxagent-site-login-admin").stat().st_mode & 0o777
    )
    result["installed_bridge_uid"] = Path("/usr/local/sbin/doxagent-site-login-admin").stat().st_uid
    result["vnc_listener"] = run(["ss", "-ltn", "sport = :5900"])
    result["scope_snapshot"] = json.loads(
        run(
            ["docker", "exec", "-i", SITE, "/app/.venv/bin/python", "-"],
            input=r"""
import json
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.governance_repository import GovernanceRepository
r=SiteStrategyRepository('/site-data/registry/site_strategy.sqlite3',read_only=True)
store=GovernanceRepository(r);out=[]
for scope in store.list_scopes():
    route=r.get_runtime('scope:'+scope.scope_id)
    members=[]
    for identity_id in (scope.primary_identity_id,scope.fallback_identity_id):
        if not identity_id:continue
        i=r.get_identity(identity_id);p=r.get_profile(i.profile_id);e=r.get_egress(i.egress_id)
        member=route.combinations.get(identity_id)
        members.append({'identity_id':identity_id,'profile_id':p.profile_id,'runtime':i.runtime_kind.value,
            'egress':e.egress_id,'ip':e.observed_ip,'country':store.egress_region(e),
            'auth':r.get_site_identity_auth(scope.owner_site_id,identity_id).auth_state.value,
            'operational_state':p.operational_state.value,
            'capabilities':member.capabilities if member else {}})
    out.append({'scope':scope.model_dump(mode='json'),'active':route.active_combination_id,
                'generation':route.generation,'members':members})
r.close();print(json.dumps(out))
""",
        )
    )
    result["poll_snapshot"] = json.loads(
        run(
            ["docker", "exec", "-i", "doxagent-v2-v2-message-bus-1", "/app/.venv/bin/python", "-"],
            input=r"""
import sqlite3,json
c=sqlite3.connect('file:/data/bus/bus.sqlite3?mode=ro',uri=True);c.execute('pragma query_only=on')
out=[]
for raw, in c.execute('select data_json from poll_states'):
    v=json.loads(raw);out.append({k:v.get(k) for k in ('binding_id','source_id','status','last_success_at','last_error_code','next_poll_at')})
c.close();print(json.dumps(out))
""",
        )
    )
    result["test_operations"] = json.loads(
        run(
            ["docker", "exec", "-i", SITE, "/app/.venv/bin/python", "-"],
            input=r"""
import json,sqlite3
c=sqlite3.connect('file:/site-data/registry/site_strategy.sqlite3?mode=ro',uri=True)
c.execute('pragma query_only=on')
rows=c.execute("select data_json from maintenance_operations where "
    "json_extract(data_json,'$.idempotency_key') like 'release-toolchain:%' or "
    "json_extract(data_json,'$.idempotency_key') like 'release-verify-retry:%' or "
    "json_extract(data_json,'$.idempotency_key') like 'release-restore:%'").fetchall()
print(json.dumps([json.loads(v[0]) for v in rows]));c.close()
""",
        )
    )
    (ROOT / "final.json").write_text(json.dumps(result, indent=2))
    (ROOT / "final.json").chmod(0o600)
    descriptor, filename = tempfile.mkstemp(
        prefix="source-isolation-receipts-20261018-", suffix=".tar.gz", dir="/tmp"
    )
    os.close(descriptor)
    target = Path(filename)
    host_names = [
        "before.json",
        "before-final-site.json",
        "compat.json",
        "pre-supervisor.json",
        "post-supervisor.json",
        "after.json",
        "build.json",
        "build.c2a6b679.json",
        "final.json",
    ]
    site_names = [
        "regions.json",
        "network.json",
        "preview.json",
        "migration.json",
        "canary.json",
        "inventory.json",
        "maintenance-smoke.json",
        "maintenance-challenge-smoke.json",
        "maintenance-verify-smoke.json",
        "publisher-preservation.json",
        "fallback-smoke.json",
        "cold-cache.json",
    ]
    with tarfile.open(target, "w:gz") as archive:
        for prefix, base, names in [("host", ROOT, host_names), ("site", SITE_ROOT, site_names)]:
            for name in names:
                path = base / name
                if path.exists():
                    archive.add(path, arcname=prefix + "/" + name)
    account = pwd.getpwnam("ubuntu")
    os.chown(target, account.pw_uid, account.pw_gid)
    target.chmod(0o600)
    print(
        json.dumps(
            {
                "receipt_archive": str(target),
                "root_available_bytes": result["root_available_bytes"],
                "other_container_changes": result["other_container_changes"],
                "inventory_median": result["bridge_inventory_seconds"]["median"],
                "desktop_token_unreadable": result["desktop_cannot_read_admin_token"],
                "source_mismatches": sum(
                    len(v["mismatches"]) for v in result["source_provenance"].values()
                ),
                "untracked_application_files": result["untracked_application_files"],
            }
        )
    )


if __name__ == "__main__":
    main()
