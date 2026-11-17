import os,json,time,sqlite3,subprocess,shutil,hashlib,signal
signal.signal(signal.SIGHUP,signal.SIG_IGN)
signal.signal(signal.SIGTERM,lambda *args: (_ for _ in ()).throw(SystemExit('termination requested')))
from pathlib import Path
from datetime import datetime,timezone
os.nice(0);p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/read/serving-v4-20261018/v2.sqlite3');target=p.with_name('v2.compact-20261114.sqlite3');old=p.with_name('v2.precompact-20261114.sqlite3');names=['doxagent-v2-v2-api-1','doxagent-v2-v2-projector-1','doxagent-v2-v2-maintenance-1'];out={'started':datetime.now(timezone.utc).isoformat(),'stages':[]};stopped=[];frozen=None;switched=False
def cmd(args):return subprocess.run(args,capture_output=True,text=True,check=True).stdout
def stage(name,**kw):
 x=dict(stage=name,at=datetime.now(timezone.utc).isoformat(),**kw);out['stages'].append(x);print(json.dumps(x),flush=True)
def signature(db):
 tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE '%_config' ORDER BY name")];counts={}
 for t in tables:counts[t]=db.execute('SELECT count(*) FROM "'+t.replace('"','""')+'"').fetchone()[0]
 meta={}
 for t in ['generation','read_meta','checkpoints','source_epochs','schema_meta']:
  if t in tables:meta[t]=db.execute('SELECT * FROM '+t+' ORDER BY 1').fetchall()
 return {'counts':counts,'meta':meta,'schema':db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY type,name").fetchall()}
assert p.resolve()==p and not target.exists() and not old.exists()
stat=p.stat();out['before_df']=cmd(['df','-B1','/']);stage('preflight',source_bytes=stat.st_size,available=shutil.disk_usage(p.parent).free)
if shutil.disk_usage(p.parent).free<22*2**30:raise RuntimeError('need >=22GiB available')
try:
 for n in names:
  if cmd(['docker','inspect','-f','{{.State.Running}}',n]).strip()=='true':stopped.append(n)
 cmd(['docker','stop','-t','45',*stopped]);stage('read_writers_stopped',containers=stopped)
 frozen=sqlite3.connect(p,timeout=20);cp=frozen.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone();assert cp[0]==0
 frozen.execute('BEGIN IMMEDIATE');before=signature(frozen);out['before_signature']=before
 stage('source_frozen',freelist=frozen.execute('PRAGMA freelist_count').fetchone()[0])
 with sqlite3.connect(p,timeout=10) as db:db.execute('VACUUM INTO ?',(str(target),))
 os.chmod(target,stat.st_mode & 0o777);os.chown(target,stat.st_uid,stat.st_gid)
 stage('vacuum_complete',target_bytes=target.stat().st_size)
 with sqlite3.connect(target) as db:
  integrity=[r[0] for r in db.execute('PRAGMA quick_check')];assert integrity==['ok'],integrity
  after=signature(db);assert before==after,'schema/count/checkpoint mismatch'
  assert db.execute('PRAGMA freelist_count').fetchone()[0]==0
 out['verified_signature_equal']=True;out['quick_check']=integrity
 stage('verified')
 # All application clients remain stopped; release reserved lock only for atomic swap.
 frozen.rollback();frozen.close();frozen=None
 for suffix in ['-wal','-shm']:
  q=Path(str(p)+suffix)
  if q.exists():q.unlink()
 os.replace(p,old)
 try:os.replace(target,p)
 except BaseException:os.replace(old,p);raise
 switched=True;stage('switched')
 cmd(['docker','start',*stopped]);stage('containers_started')
 # Opening the replacement proves it is readable after path cutover.
 with sqlite3.connect(p.as_uri()+'?mode=ro',uri=True) as db:assert db.execute('SELECT count(*) FROM generation').fetchone()[0]==before['counts']['generation']
 status={n:cmd(['docker','inspect','-f','{{.State.Running}}',n]).strip() for n in stopped};assert all(v=='true' for v in status.values())
 # Source itself was the rollback point during validation; exit it now, no extra full backup.
 out['old_allocated_removed']=old.stat().st_blocks*512;old.unlink();out['new_allocated']=p.stat().st_blocks*512
 out['after_df']=cmd(['df','-B1','/']);out['status']=status;stage('old_source_retired',after_df=out['after_df'])
except BaseException as e:
 out['error']=repr(e)
 if frozen:
  try:frozen.rollback();frozen.close()
  except Exception:pass
 if not switched and target.exists():target.unlink()
 raise
finally:
 for n in stopped:
  subprocess.run(['docker','start',n],capture_output=True,text=True)
 print(json.dumps({'result':out}),flush=True)
