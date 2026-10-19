"""Read-only bounded timings, query plans, and mapper profile."""
import subprocess
code='''
import cProfile,pstats,io,json,time,sqlite3
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.projectors import DomainProjectors
from doxagent.v2_read.source_lifecycle import BUSINESS_FACT_KINDS
s=ReadStore('/data/read/active.json');source=SourceOutbox('/data/bus/bus.sqlite3','bus')
def timed(label, fn):
 t=time.monotonic();v=fn();print(label,round(time.monotonic()-t,4),v)
with s.connect() as db:
 print('health',json.dumps([dict(r) for r in db.execute('select * from source_health')]))
 print('plans',list(map(tuple,db.execute("explain query plan SELECT * FROM object_current WHERE kind IN (SELECT value FROM json_each(?)) AND valid_from>?",(json.dumps(BUSINESS_FACT_KINDS),s.highwater(db)-1000)))))
 timed('sync_incremental',lambda:len(db.execute("SELECT * FROM object_current WHERE kind IN (SELECT value FROM json_each(?)) AND valid_from>?",(json.dumps(BUSINESS_FACT_KINDS),s.highwater(db)-1000)).fetchall()))
 timed('all_policy_catalog',lambda:len(db.execute("select payload from object_current where kind='policy_catalog'").fetchall()))
 print('indexes',list(map(tuple,db.execute("select name,sql from sqlite_master where type='index' and tbl_name='object_current'"))))
pos=s.position('bus');coords=source.coordinates(pos,100)
timed('100_event_reads',lambda:len([source.event(c) for c in coords]))
p=cProfile.Profile();p.enable();mapper=DomainProjectors(s)
for c in coords:
 e=source.event(c);e['source']='bus'
 try:mapper(e)
 except (KeyError,ValueError):pass
p.disable();stream=io.StringIO();pstats.Stats(p,stream=stream).sort_stats('cumtime').print_stats(18);print(stream.getvalue())
with source.connection() as db:
 print('transport_tail',list(map(tuple,db.execute("select seq,recorded_at,table_name from v2_source_outbox where seq>? order by seq limit 3",(pos,)))))
print('done')
'''
p=subprocess.run(['docker','exec','-i','doxagent-v2-v2-projector-1','python','-'],input=code,text=True,capture_output=True,timeout=150)
print(p.stdout);print(p.stderr[-1800:]);assert p.returncode==0
