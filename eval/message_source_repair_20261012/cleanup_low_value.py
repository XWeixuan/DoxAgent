"""Remove only explicit obsolete staging copies and reproducible dependencies."""
import json, os, shutil, subprocess
from pathlib import Path

cache=Path('/home/ubuntu/.cache/doxagent-codex-upgrade-20261002')
targets=[cache/name for name in ('agent','guardian','template','worker','research','scheduler')]
targets.append(Path('/home/ubuntu/doxagent-stage/ibgateway-linux.sh'))
for name in ('doxagent-challenge','doxagent-driver-prod','doxagent-luna-recovery',
             'doxagent-barrons-egress-build','doxagent-case-release-d11f7fa1',
             'doxagent-release-f3da669b','doxagent-stage'):
    targets.append(Path('/home/ubuntu')/name/'exports/mu_enrichment_20260915_support/node_modules')
containers=json.loads(subprocess.check_output(['docker','inspect',*subprocess.check_output(['docker','ps','-aq'],text=True).split()],text=True))
mounts=[Path(m['Source']).resolve() for c in containers for m in c.get('Mounts',[])]
cwd=[]
for p in Path('/proc').glob('[0-9]*/cwd'):
    try: cwd.append(p.resolve(strict=True))
    except (OSError,RuntimeError): pass
removed=[]
for target in targets:
    resolved=target.resolve()
    assert resolved.is_relative_to('/home/ubuntu') and len(resolved.parts)>=5
    if not target.exists():continue
    if any(m==resolved or m.is_relative_to(resolved) or resolved.is_relative_to(m) for m in mounts):
        print(json.dumps({'retained_mount':str(target)}));continue
    if any(c==resolved or c.is_relative_to(resolved) for c in cwd):
        print(json.dumps({'retained_cwd':str(target)}));continue
    check=subprocess.run(['lsof','-t','+D',str(target)],capture_output=True,text=True,timeout=30)
    if check.stdout.strip():print(json.dumps({'retained_open':str(target)}));continue
    if check.returncode not in (0,1):raise RuntimeError('open_file_check_failed')
    if resolved.is_dir():shutil.rmtree(resolved)
    else:resolved.unlink()
    removed.append(str(target))
print(json.dumps({'removed':removed,'free_bytes':shutil.disk_usage('/').free}))
