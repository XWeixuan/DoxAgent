import json, sqlite3, time, hashlib, inspect
from doxagent.api_v2.policies import selection
from doxagent.v2_read.repository import ReadStore
store = ReadStore('/data/read/v2.sqlite3')
db = sqlite3.connect(store.path.as_uri() + '?mode=ro', uri=True, timeout=1)
db.execute('PRAGMA query_only=ON')
out = {'policy_source_sha256': hashlib.sha256(inspect.getsourcefile(selection) and open(inspect.getsourcefile(selection),'rb').read()).hexdigest(), 'indexes':{}, 'counts':{}, 'queries':[]}
for table in ['objects','object_current','contributions','metric_buckets']:
    out['indexes'][table] = [r[0] for r in db.execute("SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name=?", (table,))]
seq = db.execute('SELECT max(seq) FROM commits').fetchone()[0]
for ticker in ['BE','MU','INTC']:
    out['counts'][ticker] = db.execute("SELECT count(*), count(distinct json_extract(payload,'$.policy_id')) FROM object_current WHERE kind='policy_catalog' AND ticker=?",(ticker,)).fetchone()
    view = db.execute("SELECT payload FROM views WHERE json_extract(payload,'$.wire.page')='POLICIES' AND json_extract(payload,'$.wire.ticker')=? ORDER BY expires_at DESC LIMIT 1",(ticker,)).fetchone()
    v=json.loads(view[0]) if view else None
    frozen=v['seq'] if v else seq-1
    days=v['wire']['period']['current']['trading_days'] if v else ['2026-10-13']
    for label,watermark in [('current',seq),('frozen',frozen)]:
        sql,args=selection(ticker,watermark,'ALL',days)
        sql=sql.replace('FROM objects ','FROM '+store.snapshot_table(watermark)+' ')
        query="SELECT coalesce(sum(ACTIVE OR ACTIVE_UNKNOWN),0),coalesce(sum(ADDED),0),coalesce(sum(HIT),0),coalesce(sum(MODIFIED),0),coalesce(sum(RETIRED),0),coalesce(sum(EXECUTED),0) FROM ("+sql+")"
        plan=[r[3] for r in db.execute('EXPLAIN QUERY PLAN '+query,args)]
        start=time.perf_counter()
        db.set_progress_handler(lambda: int(time.perf_counter()-start>1.5),1000)
        try:
            result=db.execute(query,args).fetchone(); error=None
        except sqlite3.OperationalError as e:
            result=None;error=str(e)
        finally:db.set_progress_handler(None,0)
        out['queries'].append({'ticker':ticker,'scope':label,'seq':watermark,'days':days,'elapsed_ms':round((time.perf_counter()-start)*1000,1),'result':result,'error':error,'plan':plan})
    details=[json.loads(r[0]) for r in db.execute("SELECT payload FROM object_current WHERE kind='policy_detail' AND ticker=? LIMIT 3",(ticker,))]
    out.setdefault('reference_samples',{})[ticker]=[{'policy_id':d['policy']['policy_id'],'document2':d.get('source_document2'),'refs':d['policy'].get('ref',[])[:5]} for d in details]
print(json.dumps(out,ensure_ascii=False))
