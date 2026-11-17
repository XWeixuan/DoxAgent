"""Retire audited local branches using ordinary git branch -d only."""
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent


def git(*args):
    return subprocess.run(['git', '--no-optional-locks', '-C', str(ROOT), *args], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=120)


inventory = json.loads((OUT / 'branch-summary.json').read_text(encoding='utf-8'))
assert len(inventory) == 55
assert git('branch', '--show-current').stdout.strip() == 'main'
assert len(json.loads((OUT / 'function-comparison.json').read_text())) == 9
assert len(json.loads((OUT / 'merged-content-review.json').read_text())) == 44
before_refs = git('for-each-ref', '--format=%(refname:short)|%(objectname)', 'refs/heads').stdout
(OUT / 'refs-execution-before.txt').write_text(before_refs, encoding='utf-8')
remote_before = git('ls-remote', '--heads', 'origin')
assert remote_before.returncode == 0
(OUT / 'remote-live-execution-before.txt').write_text(remote_before.stdout, encoding='utf-8')
dirty = git('diff', '--name-only').stdout.splitlines()
hashes = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in dirty if (ROOT / p).is_file()}
results = []
for row in inventory:
    name = row['name']
    if name == 'main':
        continue
    assert name.startswith(('codex/', 'rescue/', 'server-wip/'))
    current = git('rev-parse', '--verify', 'refs/heads/' + name)
    used = {line[7:] for line in git('worktree', 'list', '--porcelain').stdout.splitlines() if line.startswith('branch ')}
    if current.returncode != 0 or current.stdout.strip() != row['head']:
        results.append({'branch': name, 'result': 'preserved_tip_changed', 'expected_head': row['head']})
        continue
    if 'refs/heads/' + name in used:
        results.append({'branch': name, 'result': 'preserved_in_use', 'head': row['head']})
        continue
    result = git('branch', '-d', '--', name)
    record = {'branch': name, 'head': row['head'], 'result': 'deleted' if result.returncode == 0 else 'normal_delete_refused', 'exit_code': result.returncode, 'stdout': result.stdout.strip(), 'stderr': result.stderr.strip()}
    results.append(record)
    (OUT / 'deletion-results.json').write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')
    print(record['result'], name, flush=True)
after = git('for-each-ref', '--format=%(refname:short)|%(objectname)', 'refs/heads').stdout
(OUT / 'branches-after.txt').write_text(after, encoding='utf-8')
remote_after = git('ls-remote', '--heads', 'origin')
assert remote_after.returncode == 0
(OUT / 'remote-live-after.txt').write_text(remote_after.stdout, encoding='utf-8')
remaining = dict(line.split('|') for line in after.splitlines())
assert 'main' in remaining and git('branch', '--show-current').stdout.strip() == 'main'
assert all(r['branch'] not in remaining for r in results if r['result'] == 'deleted')
assert all(r['branch'] in remaining for r in results if r['result'] != 'deleted')
verification = {
    'initial_local_branches': 55, 'deleted': sum(r['result'] == 'deleted' for r in results),
    'migrated': 0, 'remaining': list(remaining), 'remaining_count': len(remaining),
    'remote_heads': len(remote_after.stdout.splitlines()), 'remote_unchanged_during_execution': remote_before.stdout == remote_after.stdout,
    'worktrees_after': git('worktree', 'list', '--porcelain').stdout,
    'dirty_file_hashes_unchanged': {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == sha for p, sha in hashes.items()},
    'staged_changes_after': git('diff', '--cached', '--name-only').stdout.splitlines(),
}
(OUT / 'execution-verification.json').write_text(json.dumps(verification, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(verification, ensure_ascii=False, indent=2))
