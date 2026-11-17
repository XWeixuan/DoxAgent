"""Prune only the 13 reviewed records whose directories do not exist."""
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[3]
OUT = Path(__file__).resolve().parent


def git(*args):
    return subprocess.run(['git', '--no-optional-locks', '-C', str(ROOT), *args], capture_output=True, text=True, encoding='utf-8', check=True)


prior = json.loads((ROOT / 'artifacts/audits/worktree_inventory_20261111/inventory.json').read_text(encoding='utf-8'))
approved = {str(Path(d['worktree'])): d for d in prior if not d['directory_exists']}
assert len(approved) == 13
raw = git('worktree', 'list', '--porcelain').stdout
records = []
for block in raw.strip().split('\n\n'):
    item = dict(line.partition(' ')[::2] for line in block.splitlines())
    records.append(item)
stale = [r for r in records if 'prunable' in r]
assert {str(Path(r['worktree'])) for r in stale} == set(approved)
for path, old in approved.items():
    assert Path(os.path.splitdrive(path)[0] + '/').is_dir()
    assert not os.path.lexists(path), path
    git('show-ref', '--verify', '--quiet', old['branch'])
assert len(records) == 14 and str(Path(records[0]['worktree'])) == str(ROOT)
before_refs = git('for-each-ref', '--format=%(refname) %(objectname)', 'refs/heads').stdout
dry = git('worktree', 'prune', '--dry-run', '--verbose', '--expire', 'now')
dry_text = dry.stdout + dry.stderr
assert len([line for line in dry_text.splitlines() if line.startswith('Removing worktrees/')]) == 13
(OUT / 'execution-prune-dry-run.txt').write_text(dry_text, encoding='utf-8')
done = git('worktree', 'prune', '--verbose', '--expire', 'now')
(OUT / 'execution-prune-output.txt').write_text(done.stdout + done.stderr, encoding='utf-8')
after = git('worktree', 'list', '--porcelain').stdout
assert after.count('worktree ') == 1 and 'prunable' not in after
after_refs = git('for-each-ref', '--format=%(refname) %(objectname)', 'refs/heads').stdout
assert before_refs == after_refs, 'Concurrent branch update; prune itself does not change branches'
assert git('branch', '--show-current').stdout.strip() == 'main'
(OUT / 'worktrees-after.txt').write_text(after, encoding='utf-8')
print(json.dumps({'pruned_records': len(stale), 'remaining_worktrees': 1, 'local_branch_count': len(after_refs.splitlines()), 'branch_refs_unchanged_by_prune': True}))
