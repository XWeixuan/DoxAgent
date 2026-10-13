"""Targeted release operations. Run as root on the deployment host."""
import argparse
import os
import gzip
import hashlib
import json
import sqlite3
import subprocess
import shutil
import fcntl
from contextlib import ExitStack
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('/home/ubuntu/v21-production-20261012')
DATA = Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
BACKUP = DATA / 'production-backups/v21-20261012'
NAMES = ['codex-worker', 'v2-api', 'v2-web', 'v2-initialization',
         'initialization-guardian', 'v2-o4', 'v2-projector', 'v2-delivery',
         'v2-executor', 'v2-control', 'v2-cdecr-executor', 'v2-scheduler',
         'v2-message-bus', 'v2-content-enrichment', 'v2-site-access',
         'v2-chrome-supervisor']
DBS = dict(research='research/research.sqlite3', initialization='initialization/control.sqlite3',
           runtime='runtime/runtime.sqlite3', bus='bus/bus.sqlite3', read='read/v2.sqlite3',
           scheduler='scheduler/scheduler.sqlite3', usage='usage/usage.sqlite3',
           o4='o4/o4.sqlite3', worker='workspaces/worker-jobs.sqlite3')


def run(args, timeout=180):
    result = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr[-1800:] or result.stdout[-1800:])
    return result.stdout


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2))
    path.chmod(0o600)


def inspect():
    return {name: json.loads(run(['docker', 'inspect', 'doxagent-v2-'+name+'-1']))[0]
            for name in NAMES}


def semantic_snapshot():
    result = {}
    for name in ('initialization', 'bus', 'runtime'):
        db = sqlite3.connect((DATA / DBS[name]).as_uri()+'?mode=ro', uri=True)
        db.row_factory = sqlite3.Row
        if name == 'initialization':
            result['active_revisions'] = [json.loads(r[0]) for r in db.execute('SELECT r.payload FROM activation_revisions r JOIN ticker_active_revision a ON a.revision_id=r.revision_id')]
        if name == 'bus':
            tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")]
            result['monitoring_tables'] = {}
            for table in tables:
                if any(token in table for token in ('binding', 'monitoring_state', 'source_setting', 'ticker_config')):
                    rows = sorted(json.dumps(dict(r), sort_keys=True) for r in db.execute('SELECT * FROM "'+table+'"'))
                    result['monitoring_tables'][table] = dict(count=len(rows), sha256=hashlib.sha256('\n'.join(rows).encode()).hexdigest())
        if name == 'runtime':
            result['control_rows'] = [dict(r) for r in db.execute('SELECT * FROM v2_ticker_control')]
        db.close()
    return result


def inventory():
    containers = inspect()
    write(ROOT / 'private-container-baseline.json', containers)
    jobs = sqlite3.connect(DATA / DBS['worker'])
    pending = []
    for identity, status, request in jobs.execute("SELECT id,status,request FROM jobs WHERE status IN ('queued','running')"):
        req = json.loads(request) if request else {}
        pending.append(dict(id=identity, status=status, run_id=req.get('run_id'), node=req.get('node')))
    summary = dict(at=datetime.now(timezone.utc).isoformat(), pending_worker_jobs=pending,
                   containers={k:dict(id=v['Id'], image=v['Config']['Image'], status=v['State']['Status'],
                       restart_count=v['RestartCount'], pid=v['State']['Pid'],
                       compose=v['Config']['Labels'].get('com.docker.compose.project.config_files')) for k,v in containers.items()},
                   semantics=semantic_snapshot())
    write(ROOT/'before.json', summary)
    print(json.dumps(summary))


def backup():
    with ExitStack() as stack:
        for relative in ('runtime/runtime.migration', 'runtime/runtime.sqlite3',
                         'runtime/runtime.sqlite3.v2-control', 'runtime/runtime.sqlite3.v2-delivery',
                         'scheduler/scheduler.sqlite3.v2-scheduler', 'read/v2.sqlite3.projector'):
            handle = stack.enter_context((DATA / (relative+'.executor.lock')).open('a+b'))
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return backup_locked()


def sha256(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(4*1024*1024):
            result.update(chunk)
    return result.hexdigest()


def backup_locked():
    BACKUP.mkdir(parents=True, exist_ok=True)
    prior_manifest=BACKUP/'backup-manifest.partial.json'
    manifest = json.loads(prior_manifest.read_text()) if prior_manifest.exists() else dict(at=datetime.now(timezone.utc).isoformat(), databases={})
    for name, relative in DBS.items():
        path = DATA / relative
        if not path.exists():
            continue
        if name in manifest['databases']:
            item=manifest['databases'][name]
            assert path.stat().st_size==item['size'] and sha256(path)==item['sha256'], 'frozen source changed: '+name
            continue
        db = sqlite3.connect(path, timeout=60)
        checkpoint = db.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
        if checkpoint[0] != 0 or checkpoint[1] not in (0, -1):
            raise ValueError(f'writer still active: {name}:{checkpoint}')
        check = db.execute('PRAGMA quick_check').fetchall() if path.stat().st_size < 1024**3 else None
        db.execute('SELECT count(*) FROM sqlite_master').fetchone()
        if check is not None and check != [('ok',)]:
            raise ValueError(f'database not healthy: {name}:{check[:3]}')
        db.close()
        size, sha = 0, hashlib.sha256()
        archive = BACKUP / (name+'.sqlite3.gz')
        with path.open('rb') as source, archive.open('wb') as target:
            compressor=subprocess.Popen(['pigz','-1','-p','2','-c'],stdin=subprocess.PIPE,stdout=target)
            while chunk := source.read(4*1024*1024):
                compressor.stdin.write(chunk)
                size += len(chunk)
                sha.update(chunk)
            compressor.stdin.close()
            assert compressor.wait()==0, 'compression failed'
        verify, verify_size = hashlib.sha256(), 0
        with gzip.open(archive, 'rb') as source:
            while chunk := source.read(4*1024*1024):
                verify.update(chunk)
                verify_size += len(chunk)
        if (size, sha.hexdigest()) != (verify_size, verify.hexdigest()):
            raise ValueError('backup verification failed: '+name)
        manifest['databases'][name] = dict(source='/data/'+relative,
            archive='/data/production-backups/v21-20261012/'+archive.name,
            size=size, sha256=sha.hexdigest(), compressed_size=archive.stat().st_size,
            archive_sha256=sha256(archive), quick_check='ok' if check else 'DEFERRED_LARGE_DATABASE',
            verification='WAL_ZERO_DECOMPRESSED_SIZE_AND_SHA256_MATCH_FROZEN_SOURCE')
        write(BACKUP/'backup-manifest.partial.json', manifest)
        print(json.dumps(dict(completed=name, **manifest['databases'][name])), flush=True)
    write(BACKUP/'backup-manifest.json', manifest)


def rollout():
    baseline = json.loads((ROOT/'private-container-baseline.json').read_text())
    guardian = baseline['initialization-guardian']
    guardian_env = dict(item.split('=',1) for item in guardian['Config']['Env'])
    os.environ['DOXAGENT_INITIALIZATION_REPAIR_SOURCE_REPOSITORY'] = next(m['Source'] for m in guardian['Mounts'] if m['Destination']=='/repair-source')
    os.environ['DOXAGENT_INITIALIZATION_REPAIR_PRODUCTION_CONTAINER'] = guardian_env['DOXAGENT_INITIALIZATION_REPAIR_PRODUCTION_CONTAINER']
    selected = [n for n in NAMES if n not in ('v2-content-enrichment','v2-site-access','v2-chrome-supervisor')]
    readfix_only = os.environ.get('V21_READFIX_ONLY') == '1'
    if readfix_only:
        selected = ['v2-api', 'v2-projector']
    receipt = {}
    for name in selected:
        prior = baseline[name]
        service = prior['Config']['Labels']['com.docker.compose.service']
        image = 'doxagent-v2-web:v21-production-20261012-r1' if name == 'v2-web' else 'doxagent-v2:v21-production-20261012-r1'
        if readfix_only:
            image = 'doxagent-v2:v21-production-20261012-r2'
        # Preserve the exact effective environment, including recent source repair settings.
        environment = dict(item.split('=',1) for item in prior['Config']['Env'])
        override = ROOT / ('compose-'+name+'.yaml')
        effective_environment = {k:v.replace('$','$$') for k,v in environment.items()}
        override.write_text('services:\n  '+service+':\n    image: '+json.dumps(image)+'\n    env_file: !reset []\n    environment: !override '+json.dumps(effective_environment)+'\n')
        override.chmod(0o600)
        command = ['docker','compose','-p','doxagent-v2','--env-file','/home/ubuntu/doxagent/.env.v2']
        for config in prior['Config']['Labels']['com.docker.compose.project.config_files'].split(','):
            assert Path(config).is_file(), config
            command += ['-f',config]
        command += ['-f',str(override)]
        run(command+['config','--quiet'])
        run(command+['up','-d','--no-deps','--no-build',service], timeout=240)
        current = json.loads(run(['docker','inspect','doxagent-v2-'+name+'-1']))[0]
        assert set(current['Config']['Env']) == set(prior['Config']['Env']), 'environment changed: '+name
        assert sorted((m['Destination'],m['Source'],m['RW']) for m in current['Mounts']) == sorted((m['Destination'],m['Source'],m['RW']) for m in prior['Mounts']), 'mounts changed: '+name
        receipt[name] = dict(before=prior['Id'], after=current['Id'], image=current['Image'], status=current['State']['Status'])
        write(ROOT/('rollout-readfix-receipt.json' if readfix_only else 'rollout-receipt.json'), receipt)
        print(json.dumps({name:receipt[name]}), flush=True)


def smoke_worker():
    baseline = json.loads((ROOT/'private-container-baseline.json').read_text())['codex-worker']
    env = dict(item.split('=',1) for item in baseline['Config']['Env'])
    home = DATA / 'v21-production-validation/codex-home'
    home.mkdir(parents=True, exist_ok=True)
    original = DATA / env.get('CODEX_HOME','/data/codex-home').removeprefix('/data/')
    for name in ('auth.json','config.toml'):
        if (original/name).is_file():
            shutil.copyfile(original/name, home/name)
            (home/name).chmod(0o600)
    env.update(CODEX_HOME='/data/v21-production-validation/codex-home',
        DOXAGENT_CODEX_CONTAINER_ISOLATION='true',
        DOXAGENT_CODEX_WORKSPACE_ROOT='/data/v21-production-validation/workspaces',
        DOXAGENT_CODEX_RUNTIME_SQLITE_PATH='/data/v21-production-validation/research.sqlite3',
        DOXAGENT_PERSISTENT_RUNTIME_V2_SQLITE_PATH='/data/v21-production-validation/runtime.sqlite3',
        DOXAGENT_TICKER_INITIALIZATION_CONTROL_PATH='/data/v21-production-validation/control.sqlite3',
        DOXAGENT_CODEX_WORKER_BASE_URL='http://v21-production-smoke-worker:8791')
    envfile=ROOT/'private-smoke.env'
    envfile.write_text('\n'.join(k+'='+v for k,v in env.items() if '\n' not in v)+'\n')
    envfile.chmod(0o600)
    output=run(['docker','run','-d','--name','v21-production-smoke-worker',
        '--network','doxagent-v2_default','--memory','2g','--cpus','1',
        '--env-file',str(envfile),'-v','doxagent-v2_v2-data:/data',
        '-v',str(ROOT)+':/ops:ro','doxagent-v2:v21-production-20261012-r1',
        'python','-m','doxagent.codex_worker'])
    print(output.strip())


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['inventory','backup','rollout','snapshot','smoke_worker'])
    command = parser.parse_args().command
    if command == 'snapshot':
        print(json.dumps(semantic_snapshot()))
    else:
        globals()[command]()
