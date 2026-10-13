"""Windows offhost pull: verify immutable archives before acknowledging retirement."""
import hashlib
import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

HOST = 'doxagent-sg'
REMOTE = '/var/lib/docker/volumes/doxagent-v2_v2-data/_data/storage-archive'
DESTINATION = Path('D:/DoxAgentPilot/backups/storage-archive')


def ssh(*args, **kwargs):
    return subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', HOST, *args],
                          check=True, **kwargs)


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def pull():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    query = ("import pathlib,json;root=pathlib.Path(" + repr(REMOTE) + ");"
             "print(json.dumps([{'name':p.name,'value':json.loads(p.read_text())} "
             "for p in root.glob('*.manifest.json')]))")
    output = ssh('sudo', '-n', 'python3', '-', input=query, text=True, capture_output=True)
    values = json.loads(output.stdout)
    receipts = []
    for entry in values:
        manifest = entry['value']
        if manifest.get('format') != 'doxagent.distribution-cold.1':
            continue
        name = manifest['file']
        if not re.fullmatch(r'realtime-rejects-[a-zA-Z0-9.-]+\.jsonl\.gz', name):
            raise ValueError('invalid archive name')
        target = DESTINATION / name
        if not target.exists():
            if shutil.disk_usage(DESTINATION).free < manifest['bytes'] + 256 * 1024**2:
                raise ValueError('D drive has insufficient offhost staging reserve')
            partial = target.with_suffix('.partial')
            with partial.open('wb') as stream:
                ssh('sudo', '-n', 'cat', REMOTE + '/' + name, stdout=stream)
                stream.flush()
                import os
                os.fsync(stream.fileno())
            if partial.stat().st_size != manifest['bytes'] or sha(partial) != manifest['sha256']:
                raise ValueError('offhost archive checksum mismatch')
            partial.replace(target)
        elif target.stat().st_size != manifest['bytes'] or sha(target) != manifest['sha256']:
            raise ValueError('existing offhost archive checksum mismatch')
        receipt = dict(verified=True, sha256=manifest['sha256'], bytes=manifest['bytes'],
                       offhost_path=str(target.resolve()), verified_at=datetime.now(UTC).isoformat())
        (DESTINATION / entry['name']).write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        ack_name = entry['name'].replace('.manifest.json', '.offhost.json')
        (DESTINATION / ack_name).write_text(json.dumps(receipt, indent=2), encoding='utf-8')
        code = ("import pathlib,json,os;root=pathlib.Path(" + repr(REMOTE) + ");"
                "manifest=json.loads((root/" + repr(entry['name']) + ").read_text());"
                "receipt=" + repr(receipt) + ";"
                "assert manifest['sha256']==receipt['sha256'] and manifest['bytes']==receipt['bytes'];"
                "p=root/" + repr(ack_name) + ";t=p.with_suffix('.tmp');"
                "f=t.open('w');json.dump(receipt,f);f.flush();os.fsync(f.fileno());f.close();t.replace(p)")
        ssh('sudo', '-n', 'python3', '-', input=code, text=True, capture_output=True)
        receipts.append(receipt)
        print(json.dumps(receipt), flush=True)
    return receipts


if __name__ == '__main__':
    DESTINATION.mkdir(parents=True, exist_ok=True)
    report = dict(at=datetime.now(UTC).isoformat(), receipts=[], error=None)
    try:
        report['receipts'] = pull()
    except Exception as exc:
        report['error'] = str(exc)
        raise
    finally:
        (DESTINATION / 'last-pull.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
