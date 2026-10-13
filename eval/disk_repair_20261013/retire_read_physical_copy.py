"""Retire only the accepted Read precompact inode, never its shared content files."""
import json
import shutil
import subprocess
from pathlib import Path
from fenced_bus_compaction import handles

work=Path('/home/ubuntu/disk-repair-20261013')
cutover=json.loads((work/'read-compaction.json').read_text())
accepted=json.loads((work/'read-acceptance.json').read_text())
assert accepted['verified'] and accepted['objects_checked']>0
assert accepted['current_seq']>accepted['preserved_seq'], 'projector has not advanced'
old=Path(cutover['retired_path'])
current=old.with_name('v2.sqlite3')
assert old.name=='v2.disk-repair-20261013.precompact.sqlite3'
assert old.stat().st_ino!=current.stat().st_ino
for name in ['doxagent-v2-v2-api-1','doxagent-v2-v2-projector-1','doxagent-v2-v2-maintenance-1']:
    state=json.loads(subprocess.check_output(['docker','inspect',name]))[0]['State']
    assert state['Running'], name
report=dict(verified=True,before_free=shutil.disk_usage(old.parent).free,files=[])
for path in [old,Path(str(old)+'-wal'),Path(str(old)+'-shm')]:
    if not path.exists():
        continue
    assert not path.is_symlink() and not handles(path), str(path)
    if path!=old:
        assert path.stat().st_size<=32768 and (not path.name.endswith('-wal') or path.stat().st_size==0)
    report['files'].append(dict(path=str(path),bytes=path.stat().st_size))
    path.unlink()
report['after_free']=shutil.disk_usage(current.parent).free
(work/'read-physical-retirement.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report),flush=True)
