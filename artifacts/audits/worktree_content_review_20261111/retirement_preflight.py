"""Verify the user-approved retirement manifest without changing worktrees."""
import csv
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent


def git(path, *args):
    return subprocess.check_output(
        ['git', '--no-optional-locks', '-C', str(path), *args], timeout=90
    )


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


assert ROOT == Path(r'C:\Users\WEIXUANXIE\Desktop\DoxAgent')
assert git(ROOT, 'branch', '--show-current').decode().strip() == 'main'
trees = json.loads((OUT / 'trees.json').read_text(encoding='utf-8'))
with zipfile.ZipFile(OUT / 'review-evidence.zip') as z:
    decisions = list(csv.DictReader(z.read('file-decisions.csv').decode('utf-8-sig').splitlines()))
    manifest = [json.loads(x) for x in z.read('file-manifest.jsonl').decode().splitlines()]
by_tree = {}
for row in manifest:
    by_tree.setdefault(row['tree'], {})[row['path']] = row
result = []
for tree in trees:
    p = Path(tree['path'])
    assert p.resolve() != ROOT and p.is_dir(), p
    assert git(p, 'rev-parse', 'HEAD').decode().strip() == tree['head'], p
    assert not git(p, 'rev-list', 'origin/main..HEAD').strip(), p
    assert not git(p, 'diff', '--cached', '--name-only').strip(), p
    expected = by_tree.get(tree['key'], {})
    # The relevance worktree has no extra files, and therefore no manifest rows.
    current = set()
    for extra in ([], ['--ignored']):
        current.update(x for x in git(p, 'ls-files', '--others', '--exclude-standard', '-z', *extra).decode().split('\0') if x)
    added = sorted(current - set(expected))
    missing = sorted(set(expected) - current)
    changed = []
    for rel in sorted(current & set(expected)):
        row = expected[rel]
        if row.get('sha256') and digest(p / rel) != row['sha256']:
            changed.append(rel)
    dirty = [x for x in git(p, 'diff', '--name-only').decode().splitlines() if x]
    if tree['key'] == 'DoxAgent-w1-index-20261106':
        assert dirty == [x['path'] for x in tree['changes']], dirty
        for row in tree['changes']:
            actual = git(p, 'diff', 'HEAD', '--', row['path']).decode('utf-8')
            assert actual.replace('\r\n', '\n') == (OUT / row['diff_file']).read_text(encoding='utf-8').replace('\r\n', '\n'), row['path']
    else:
        assert not dirty, (p, dirty)
    record = {'key': tree['key'], 'path': str(p.resolve()), 'head': tree['head'], 'new_files': added, 'missing_files': missing, 'changed_files': changed, 'tracked_changes': dirty, 'ready': not added and not changed and not missing}
    result.append(record)
    print(tree['key'], 'READY' if record['ready'] else 'DRIFT', 'extra_files', len(current), 'new', len(added), 'changed', len(changed), 'missing', len(missing), flush=True)
(OUT / 'retirement-preflight.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
main_dirty = [x for x in git(ROOT, 'diff', '--name-only').decode().splitlines() if x]
snapshot = {'head': git(ROOT, 'rev-parse', 'HEAD').decode().strip(), 'branch': 'main', 'tracked_dirty_hashes': {f: digest(ROOT / f) for f in main_dirty}, 'changelog_bytes': (ROOT / 'changelog').stat().st_size, 'refs': git(ROOT, 'for-each-ref', '--format=%(refname) %(objectname)', 'refs/heads').decode().splitlines(), 'worktrees': git(ROOT, 'worktree', 'list', '--porcelain').decode()}
(OUT / 'execution-before.json').write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
assert all(x['ready'] for x in result), 'Manifest drift: review before removing affected worktrees.'
