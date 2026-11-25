"""Restore only two checksum-proven publication ancestors from the cold archive."""
import hashlib
import json
import subprocess
import tarfile
from pathlib import Path

ARCHIVE = Path('D:/DoxAgent-Cold/20261123-emergency/events-00200-1795430537050439400.tar.gz')
ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT/'eval/runtime_projection_recovery_20261125'
OUTPUT.mkdir(parents=True, exist_ok=True)
RUNS = {'runtime-maintain-c9231856ccb1fe39a5352133': 'COHR',
        'runtime-maintain-e85f83f138249e6aeab10a52': 'NBIS'}
receipts = [json.loads(line) for line in ARCHIVE.with_suffix('.retired.jsonl').read_text().splitlines()]
selected = [r for r in receipts if r['retired'].split('/')[0] in RUNS]
assert len(selected) == 2
with tarfile.open(ARCHIVE) as archive:
    for row in selected:
        member = archive.getmember(row['retired'])
        assert member.isfile() and member.size == row['bytes']
        raw = archive.extractfile(member).read()
        assert hashlib.sha256(raw).hexdigest() == row['sha256']
        run = row['retired'].split('/')[0]
        local = OUTPUT/(RUNS[run]+'-ancestor.sqlite3')
        local.write_bytes(raw)
        subprocess.run(['scp', '-q', str(local), 'doxagent-sg:/tmp/'+local.name], check=True)
code = '''
import hashlib,json,os,sqlite3,fcntl,time
from pathlib import Path
rows=ROWS
base=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/runtime/runtime_artifacts')
with Path('/var/lib/doxagent/releases/release.lock').open('a') as release, (base/'resource-retirement.executor.lock').open('a') as owner:
 fcntl.flock(release,fcntl.LOCK_EX|fcntl.LOCK_NB);fcntl.flock(owner,fcntl.LOCK_EX)
 for row in rows:
  relative=Path(row['retired']); ticker=relative.parts[-2]
  target=(base/relative).resolve();assert target.is_relative_to(base.resolve()) and target.parent.is_dir()
  source=Path('/tmp')/(ticker+'-ancestor.sqlite3');raw=source.read_bytes()
  assert len(raw)==row['bytes'] and hashlib.sha256(raw).hexdigest()==row['sha256']
  with sqlite3.connect(source.as_uri()+'?mode=ro',uri=True) as db:
   assert db.execute('PRAGMA quick_check').fetchone()[0]=='ok'
   versions=db.execute("SELECT ticker,version FROM library_versions WHERE status='PUBLISHED'").fetchall()
   assert versions and all(v[0]==ticker for v in versions)
  if target.exists():assert hashlib.sha256(target.read_bytes()).hexdigest()==row['sha256']
  else:
   staging=target.with_suffix('.restore-20261125')
   with staging.open('xb') as out:out.write(raw);out.flush();os.fsync(out.fileno())
   os.chmod(staging,0o644);os.link(staging,target);staging.unlink()
  print(json.dumps(dict(restored=str(target),sha256=row['sha256'],versions=versions,at=time.time())),flush=True)
'''.replace('rows=ROWS', 'rows='+repr(selected))
result = subprocess.run(['ssh', '-o', 'BatchMode=yes', 'doxagent-sg', 'sudo python3 -'],
                        input=code, text=True, capture_output=True, check=True)
(OUTPUT/'ancestor-restoration.jsonl').write_text(result.stdout, encoding='utf-8')
print(result.stdout)
