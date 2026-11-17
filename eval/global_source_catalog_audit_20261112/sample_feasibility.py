import subprocess

CODE = r'''import os,json,sqlite3,time
from datetime import datetime,UTC,timedelta
from doxagent.v2_read.repository import ReadStore
store=ReadStore(os.environ['DOXAGENT_V2_READ_SQLITE_PATH'])
db=sqlite3.connect(store.path.as_uri()+'?mode=ro',uri=True,timeout=.3)
started=time.monotonic();db.set_progress_handler(lambda: time.monotonic()-started>5,10000)
bs=[dict(ticker=t,source=s) for t,s in db.execute("SELECT ticker,source_id FROM object_current INDEXED BY current_identity WHERE kind='native:ticker_source_bindings' AND json_extract(payload,'$.tombstoned_at') IS NULL")]
sql="""SELECT json_extract(q.value,'$.source'),d.ticker,d.parent,
 json_extract(d.payload,'$.first_discovered_at'),json_extract(b.payload,'$.eligible'),
 json_extract(b.payload,'$.outcome'),json_extract(m.payload,'$.pipeline.discovered_at'),
 json_extract(m.payload,'$.stream_published_at')
 FROM json_each(?) q CROSS JOIN object_current d INDEXED BY current_identity
 ON d.kind='native:live_deliveries' AND d.ticker=json_extract(q.value,'$.ticker')
 AND d.id IN (SELECT x.id FROM article_sample_keys x INDEXED BY article_sample_source_time
 WHERE x.ticker=json_extract(q.value,'$.ticker') AND x.source_id=json_extract(q.value,'$.source')
 ORDER BY x.discovered_at DESC,x.id DESC LIMIT 20)
 LEFT JOIN object_current b INDEXED BY current_identity ON b.kind='native:article_body_results' AND b.ticker='' AND b.id=d.parent
 LEFT JOIN object_current m INDEXED BY current_identity ON m.kind='message' AND m.ticker=d.ticker AND m.id=json_extract(d.payload,'$.standard_id')"""
args=(json.dumps(bs),)
plan=[tuple(r) for r in db.execute('EXPLAIN QUERY PLAN '+sql,args)]
rows=db.execute(sql,args).fetchall();seconds=time.monotonic()-started
grouped={}
for row in rows:grouped.setdefault((row[0],row[1]),[]).append(row)
now=datetime.now(UTC);cutoff=now-timedelta(hours=24);selected=[]
for values in grouped.values():
 values.sort(key=lambda r:(datetime.fromisoformat(r[3]),r[2]),reverse=True)
 fresh=[r for r in values if cutoff<=datetime.fromisoformat(r[3])<=now]
 selected.extend(fresh if len(fresh)>=5 else values[:5])
articles={(r[0],r[2]) for r in selected}
print(json.dumps({'at':now.isoformat(),'query_seconds':seconds,'bindings':len(bs),'candidate_rows':len(rows),'selected_message_observations':len(selected),'unique_source_articles':len(articles),'plan':plan,'production_writes':0}))
'''
cid=subprocess.check_output(['docker','ps','-q','--filter','label=com.docker.compose.service=v2-api'],text=True).strip()
p=subprocess.run(['docker','exec','-i',cid,'python','-'],input=CODE,text=True,capture_output=True,timeout=12)
if p.returncode:
    raise RuntimeError(p.stderr[-1500:])
print(p.stdout)
