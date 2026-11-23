"""One-off verified offhost archive of explicitly authorized old diagnostics."""
import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

SYMBOLS = '--symbols' in sys.argv
DEST = Path('D:/DoxAgent-Cold/20261123-projection-recovery' + ('-symbols' if SYMBOLS else ''))
DEST.mkdir(parents=True, exist_ok=True)


def remote(code, **kwargs):
    return subprocess.run(['ssh', '-o', 'BatchMode=yes', 'doxagent-sg', 'sudo python3 -'],
                          input=code.encode(), check=True, **kwargs)


PREPARE = '''
import hashlib,json,os,time
from pathlib import Path
root=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
paths=list((root/'ops').glob('*/export/*.json'))
paths += list((root/'diagnostics').rglob('*'))
paths += [Path('/home/ubuntu/doxagent/deploy/codex-runtime/symbols/codex.debug')]
paths += [p for p in Path('/home/ubuntu/doxagent/eval').rglob('*') if p.suffix in ('.json','.jsonl','.log','.txt','.gz','.zip')]
opened=set()
for proc in Path('/proc').glob('[0-9]*/fd'):
 try:
  for fd in proc.iterdir():
   try: opened.add(fd.resolve())
   except OSError: pass
 except OSError: pass
rows=[]
for p in sorted(set(paths)):
 if p.is_symlink() or not p.is_file() or p in opened: continue
 st=p.stat()
 if st.st_mtime>time.time()-5*86400: continue
 with p.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
 rows.append(dict(path=str(p),size=st.st_size,mtime_ns=st.st_mtime_ns,sha256=digest,blocks=st.st_blocks*512))
print(json.dumps(rows))
'''
manifest = DEST/'manifest.json'
if not manifest.exists():
    prepare = PREPARE
    if SYMBOLS:
        prepare = prepare.replace("for p in sorted(set(paths)):",
                                  "for p in [Path('/home/ubuntu/doxagent/deploy/codex-runtime/symbols/codex.debug')]:")
        prepare = prepare.replace('5*86400', '2*86400')
    manifest.write_bytes(remote(prepare, capture_output=True).stdout)
rows = json.loads(manifest.read_bytes())
archive = DEST/'diagnostics.tar.gz'
if not archive.exists():
    code = "import tarfile,sys; rows=" + repr(rows) + "; t=tarfile.open(fileobj=sys.stdout.buffer,mode='w|gz');\nfor row in rows: t.add(row['path'],arcname=row['path'].lstrip('/'),recursive=False)\nt.close()\n"
    partial = archive.with_suffix('.partial')
    with partial.open('wb') as out:
        remote(code, stdout=out)
    partial.replace(archive)
expected = {r['path'].lstrip('/'): r for r in rows}
seen = set()
with tarfile.open(archive) as tar:
    for member in tar:
        assert member.isfile() and member.name in expected and member.name not in seen
        assert member.size == expected[member.name]['size']
        assert hashlib.file_digest(tar.extractfile(member), 'sha256').hexdigest() == expected[member.name]['sha256']
        seen.add(member.name)
assert seen == set(expected)
print(json.dumps(dict(verified=len(rows), allocated_bytes=sum(r['blocks'] for r in rows), archive=str(archive))),flush=True)
code = '''
import hashlib,json,os,shutil
from pathlib import Path
rows=ROWS
allowed=(Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/ops'),Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/diagnostics'),Path('/home/ubuntu/doxagent/eval'),Path('/home/ubuntu/doxagent/deploy/codex-runtime/symbols'))
opened=set()
for proc in Path('/proc').glob('[0-9]*/fd'):
 try:
  for fd in proc.iterdir():
   try: opened.add(fd.resolve())
   except OSError: pass
 except OSError: pass
result=dict(free_before=shutil.disk_usage('/').free,removed=[],skipped=[])
for row in rows:
 p=Path(row['path']); resolved=p.resolve()
 assert any(resolved.is_relative_to(base) for base in allowed) and not p.is_symlink()
 if not p.exists(): continue
 st=p.stat()
 if resolved in opened or st.st_size!=row['size'] or st.st_mtime_ns!=row['mtime_ns']:
  result['skipped'].append(str(p)); continue
 with p.open('rb') as f: digest=hashlib.file_digest(f,'sha256').hexdigest()
 if digest!=row['sha256']: result['skipped'].append(str(p)); continue
 p.unlink(); result['removed'].append(str(p))
result['free_after']=shutil.disk_usage('/').free
print(json.dumps(result))
'''.replace('rows=ROWS', 'rows='+repr(rows))
receipt=remote(code,capture_output=True).stdout
(DEST/'retirement.json').write_bytes(receipt)
result=json.loads(receipt)
print(json.dumps(dict(removed=len(result['removed']),skipped=result['skipped'],free_before=result['free_before'],free_after=result['free_after'])),flush=True)
