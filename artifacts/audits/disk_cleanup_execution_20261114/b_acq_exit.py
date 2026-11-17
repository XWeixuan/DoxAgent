import os,json,time,sqlite3
from pathlib import Path
from datetime import datetime,timezone,timedelta
os.nice(10);p=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/acquisition.sqlite3');cut=(datetime.now(timezone.utc)-timedelta(days=7)).isoformat();out={'cutoff':cut,'cycle_rows_deleted':0,'physical_rows_deleted':0,'errors':[]};db=sqlite3.connect(p,timeout=.3);db.execute('PRAGMA synchronous=NORMAL');out['free_before']=db.execute('PRAGMA freelist_count').fetchone()[0]*4096
# This index already exists in current repository schema but was absent from the deployed DB.
db.execute('CREATE INDEX IF NOT EXISTS captures_run ON captures(run_id,status)');db.commit();out['captures_run_index_present']=True
for key,sql in [('old_cycle_rows',"SELECT count(*) FROM cycles INDEXED BY cycles_time WHERE dispatch_at<?"),('old_physical_rows',"SELECT count(*) FROM physical_cycles INDEXED BY physical_cycles_time WHERE finished_at<?")]:
 start=time.monotonic();db.set_progress_handler(lambda:time.monotonic()-start>20,1000)
 try:out[key]=db.execute(sql,(cut,)).fetchone()[0]
 except sqlite3.Error as e:out['errors'].append({key:str(e)})
 db.set_progress_handler(None,0)
print(json.dumps({'stage':'preflight',**out}),flush=True)
start=time.monotonic();cursor=['','']
while time.monotonic()-start<120:
 try:
  rows=db.execute("SELECT dispatch_at,run_id,status FROM cycles INDEXED BY cycles_time WHERE dispatch_at<? AND (dispatch_at,run_id)>(?,?) ORDER BY dispatch_at,run_id LIMIT 512",(cut,*cursor)).fetchall()
  if not rows:break
  db.execute('BEGIN IMMEDIATE');before=db.total_changes
  db.executemany("DELETE FROM cycles WHERE run_id=? AND dispatch_at<? AND status NOT IN ('SCHEDULE','DISPATCHED')",[(r[1],cut) for r in rows if r[2] not in ('SCHEDULE','DISPATCHED')]);db.commit();out['cycle_rows_deleted']+=db.total_changes-before;cursor=list(rows[-1][:2]);time.sleep(.01)
 except sqlite3.OperationalError as e:
  db.rollback()
  if 'locked' in str(e):time.sleep(.05);continue
  out['errors'].append(str(e));break
out['cycle_scan_cursor']=cursor
start=time.monotonic();cursor=['','']
while time.monotonic()-start<120:
 try:
  rows=db.execute('SELECT finished_at,run_id FROM physical_cycles INDEXED BY physical_cycles_time WHERE finished_at<? AND (finished_at,run_id)>(?,?) ORDER BY finished_at,run_id LIMIT 512',(cut,*cursor)).fetchall()
  if not rows:break
  db.execute('BEGIN IMMEDIATE');before=db.total_changes
  for at,run in rows:
   if not db.execute('SELECT 1 FROM cycles WHERE run_id>=? AND run_id<? LIMIT 1',(run+':',run+';')).fetchone() and not db.execute("SELECT 1 FROM captures WHERE run_id=? AND status!='ACCEPTED' LIMIT 1",(run,)).fetchone():db.execute('DELETE FROM physical_cycles WHERE run_id=? AND finished_at<?',(run,cut))
  db.commit();out['physical_rows_deleted']+=db.total_changes-before;cursor=list(rows[-1]);time.sleep(.01)
 except sqlite3.OperationalError as e:
  db.rollback()
  if 'locked' in str(e):time.sleep(.05);continue
  out['errors'].append(str(e));break
out['physical_scan_cursor']=cursor;out['free_after']=db.execute('PRAGMA freelist_count').fetchone()[0]*4096;out['checkpoint']=db.execute('PRAGMA wal_checkpoint(PASSIVE)').fetchone();db.close();print(json.dumps({'result':out}),flush=True)
