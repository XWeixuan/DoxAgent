"""Explicitly authorized incident cleanup; no business databases or profiles."""

import argparse
import hashlib
import json
import shutil
from pathlib import Path

ROOT = Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/backups')
NAMES = ('20260927T184853Z-verified-current', 'trade-execution-20260929T122500Z')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    records = []
    for name in NAMES:
        target = ROOT / name
        if not target.exists():
            continue
        if target.is_symlink() or target.resolve() != target or target.parent != ROOT:
            raise RuntimeError('unexpected cleanup path')
        files = [p for p in target.rglob('*') if p.is_file()]
        records.append({
            'path': str(target), 'bytes': sum(p.stat().st_size for p in files),
            'file_count': len(files),
            'manifests': {p.name: {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                                 'content': p.read_text() if p.stat().st_size < 4096 else None}
                          for p in files if p.parent == target
                          and p.name in ('manifest.json', 'verification.json')},
        })
    print(json.dumps({'apply': args.apply, 'before': shutil.disk_usage(ROOT)._asdict(),
                      'targets': records}), flush=True)
    if args.apply:
        for record in records:
            shutil.rmtree(record['path'])
        print(json.dumps({'after': shutil.disk_usage(ROOT)._asdict()}), flush=True)


if __name__ == '__main__':
    main()
