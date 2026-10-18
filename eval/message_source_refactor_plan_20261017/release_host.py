"""2026-10-18 coordinated release; private receipts, no arbitrary source overlays."""

import argparse
import json
import os
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path("/var/lib/doxagent/source-isolation-release-20261018")
REPO = Path("/home/ubuntu/doxagent")
SITE = "doxagent-v2-v2-site-access-1"
SUPERVISOR = "doxagent-v2-v2-chrome-supervisor-1"


def run(args, *, script=None, timeout=180):
    return subprocess.check_output(args, input=script, text=True, cwd=REPO, timeout=timeout).strip()


def save(name, value):
    path = ROOT / name
    path.write_text(json.dumps(value, indent=2))
    path.chmod(0o600)


def inventory():
    ids = run(
        ["docker", "ps", "-q", "--filter", "label=com.docker.compose.project=doxagent-v2"]
    ).split()
    return {
        v["Name"].lstrip("/"): {
            "container": v["Id"],
            "image": v["Image"],
            "tag": v["Config"]["Image"],
        }
        for v in json.loads(run(["docker", "inspect", *ids]))
    }


def site_python(script):
    return json.loads(
        run(["docker", "exec", "-i", SITE, "/app/.venv/bin/python", "-"], script=script)
    )


def freeze(name):
    value = site_python(r"""
import json,socket,sqlite3,urllib.request
from urllib.parse import urlsplit,urlunsplit
db=sqlite3.connect('file:/site-data/registry/site_strategy.sqlite3?mode=ro',uri=True)
ids=[json.loads(v[0]) for v in db.execute('select r.spec_json from browser_identity_heads h join browser_identity_revisions r on h.identity_id=r.identity_id and h.active_revision=r.revision where h.enabled=1')]
profiles=[json.loads(v[0]) for v in db.execute('select data_json from browser_profiles')]
out={'profiles':[{k:p.get(k) for k in ('profile_id','auth_state','operational_state','challenge_identity_id','challenge_site_id','challenge_target_id')} for p in profiles],'instances':[]}
for identity in ids:
    try:
        s=socket.socket(socket.AF_UNIX);s.settimeout(3);s.connect('/run/doxagent-chrome/supervisor.sock')
        s.sendall((json.dumps({'command':'status','owner_id':'v2-site-access','identity_id':identity['identity_id']})+'\n').encode())
        status=json.loads(s.makefile().readline());s.close()
        item={k:status.get(k) for k in ('identity_id','instance_id','pid','running','known_missing')}
        item['identity_id']=identity['identity_id'];item['targets']=[]
        if status.get('running'):
            targets=json.load(urllib.request.urlopen(status['cdp_http_url']+'/json/list',timeout=3))
            for t in targets:
                u=urlsplit(t.get('url',''));safe=urlunsplit((u.scheme,u.hostname or '',u.path,'',''))
                item['targets'].append({'target_id':t.get('id'),'type':t.get('type'),'url':safe})
        out['instances'].append(item)
    except Exception as exc:out['instances'].append({'identity_id':identity['identity_id'],'error':type(exc).__name__})
tables={v[0] for v in db.execute("select name from sqlite_master where type='table'")}
if 'challenge_tasks' in tables:
    out['tasks']=[json.loads(v[0]) for v in db.execute('select data_json from challenge_tasks')]
    for task in out['tasks']:
        u=urlsplit(task['original_url']);task['original_url']=urlunsplit((u.scheme,u.hostname or '',u.path,'',''))
db.close();print(json.dumps(out))
""")
    value["at"] = datetime.now(UTC).isoformat()
    value["containers"] = inventory()
    value["root_free"] = shutil.disk_usage("/").free
    save(name, value)
    print(
        json.dumps(
            {
                "frozen": name,
                "instances": len(value["instances"]),
                "targets": sum(len(v.get("targets", [])) for v in value["instances"]),
                "tasks": len(value.get("tasks", [])),
            }
        ),
        flush=True,
    )


def compose():
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
    return command


def configure(build):
    path = REPO / ".env.v2"
    backup = ROOT / "env.before"
    if not backup.exists():
        shutil.copy2(path, backup)
        backup.chmod(0o600)
    values = {
        "DOXAGENT_V2_IMAGE": build["images"]["backend"]["tag"],
        "DOXAGENT_SITE_ACCESS_IMAGE": build["images"]["site"]["tag"],
        "DOXAGENT_CHROME_SUPERVISOR_IMAGE": build["images"]["supervisor"]["tag"],
        "DOXAGENT_BUILD_COMMIT": build["commit"],
        "DOXAGENT_SOURCE_SHA256": build["source_sha256"],
    }
    lines = path.read_text().splitlines()
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0]
        if key in values:
            lines[i] = key + "=" + values.pop(key)
    lines.extend(key + "=" + value for key, value in values.items())
    temporary = ROOT / "env.next"
    temporary.write_text("\n".join(lines) + "\n")
    temporary.chmod(path.stat().st_mode & 0o777)
    os.chown(temporary, path.stat().st_uid, path.stat().st_gid)
    os.replace(temporary, path)
    run(compose() + ["config", "--quiet"])


def healthy(name):
    for _ in range(60):
        state = json.loads(run(["docker", "inspect", name]))[0]["State"]
        if state.get("Health", {}).get("Status") == "healthy":
            return
        if not state["Running"]:
            raise RuntimeError(name + "_not_running")
        time.sleep(1)
    raise RuntimeError(name + "_not_healthy")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["freeze", "compat", "runtime", "clients"])
    args = parser.parse_args()
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.phase == "freeze":
        freeze("before-final-site.json" if (ROOT / "before.json").exists() else "before.json")
        return
    build = json.loads((ROOT / "build.json").read_text())
    if "supervisor" not in build["images"]:
        previous = json.loads((ROOT / "build.c2a6b679.json").read_text())
        protected = [
            "chrome_supervisor.py",
            "supervisor_state.py",
            "runtime.py",
            "external_runtime.py",
            "schema.py",
            "resource_gc.py",
        ]
        paths = ["src/doxagent/site_strategy/" + name for name in protected]
        paths.append("deploy/chrome-supervisor-entrypoint.sh")
        assert not run(["git", "diff", previous["commit"], build["commit"], "--", *paths])
        build["images"]["supervisor"] = {
            **previous["images"]["supervisor"],
            "source_commit": previous["commit"],
            "reuse_reason": "native runtime unchanged; avoid redundant low-space rebuild",
        }
        save("build.json", build)
    assert run(["git", "rev-parse", "HEAD"]) == build["commit"]
    assert not run(["git", "status", "--porcelain", "--untracked-files=no"])
    configure(build)
    if args.phase == "compat":
        assert (ROOT / "before.json").exists()
        run(["docker", "stop", "--time", "-1", SITE])
        run(compose() + ["up", "-d", "--no-deps", "--no-build", "v2-site-access"])
        healthy(SITE)
        for source, destination in [
            ("deploy/site-login-admin.py", "/usr/local/sbin/doxagent-site-login-admin"),
            ("deploy/site-login-ui.py", "/usr/local/bin/doxagent-site-login-ui"),
        ]:
            run(["install", "-o", "root", "-g", "root", "-m", "0755", source, destination])
        freeze("compat.json")
    elif args.phase == "runtime":
        assert (ROOT / "compat.json").exists()
        freeze("pre-supervisor.json")
        run(compose() + ["stop", "--timeout", "120", "v2-message-bus", "v2-content-enrichment"])
        run(["docker", "stop", "--time", "-1", SITE])
        run(["docker", "stop", "--time", "-1", SUPERVISOR])
        run(compose() + ["up", "-d", "--no-deps", "--no-build", "v2-chrome-supervisor"])
        healthy(SUPERVISOR)
        run(compose() + ["up", "-d", "--no-deps", "--no-build", "v2-site-access"])
        healthy(SITE)
        freeze("post-supervisor.json")
    elif args.phase == "clients":
        assert (ROOT / "post-supervisor.json").exists()
        run(
            compose()
            + ["up", "-d", "--no-deps", "--no-build", "v2-message-bus", "v2-content-enrichment"]
        )
        save(
            "after.json",
            {
                "at": datetime.now(UTC).isoformat(),
                "containers": inventory(),
                "root_free": shutil.disk_usage("/").free,
            },
        )
    print(json.dumps({"phase": args.phase, "completed": True}), flush=True)


if __name__ == "__main__":
    main()
