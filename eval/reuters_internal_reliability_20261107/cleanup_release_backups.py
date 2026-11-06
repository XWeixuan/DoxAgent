"""Remove only this repair's redundant intermediate backup copies; keep first/latest."""
import json
from pathlib import Path

root = Path('/var/lib/doxagent/releases/backups').resolve()
keep = ['reuters-internal-20261106T182527Z.sqlite3', 'reuters-internal-20261106T185320Z.sqlite3']
assert all((root / name).is_file() and (root / name).stat().st_size > 0 for name in keep)
removed = []
for name in ['reuters-internal-20261106T182604Z.sqlite3',
             'reuters-internal-20261106T182937Z.sqlite3', 'reuters-internal-20261106T184507Z.sqlite3']:
    path = root / name
    assert path.resolve().parent == root and not path.is_symlink()
    if path.is_file():
        size = path.stat().st_size
        path.unlink()
        removed.append({'file': name, 'bytes': size})
print(json.dumps({'kept': keep, 'removed': removed,
                  'bytes_released': sum(v['bytes'] for v in removed)}, indent=2))
