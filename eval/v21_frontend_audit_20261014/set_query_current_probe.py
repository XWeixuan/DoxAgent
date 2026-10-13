import json, sqlite3, time
from doxagent.v2_read.repository import ReadStore
store=ReadStore('/data/read/v2.sqlite3'); db=sqlite3.connect(store.path.as_uri()+'?mode=ro',uri=True,timeout=1)
db.execute('PRAGMA query_only=ON')
out={'catalog':{},'measurements':[],'refs':{}}
for ticker in ['BE','MU','INTC']:
    out['catalog'][ticker]=list(db.execute("SELECT json_extract(payload,'$.lifecycle'),count(*),count(distinct json_extract(payload,'$.policy_id')) FROM object_current WHERE kind='policy_catalog' AND ticker=? GROUP BY 1",(ticker,)))
    details=[json.loads(r[0]) for r in db.execute("SELECT payload FROM object_current WHERE kind='policy_detail' AND ticker=? AND json_extract(payload,'$.summary.schema_version')='document3.policy-summary.v3' ORDER BY cast(json_extract(payload,'$.summary.policy_set_version') as integer) DESC LIMIT 3",(ticker,))]
    out['refs'][ticker]=[{'id':d['policy']['policy_id'],'d2':d.get('source_document2'),'refs':d['policy']['ref'][:8]} for d in details]
    for period in ['CURRENT','ALL']:
        seq=db.execute('SELECT max(seq) FROM commits').fetchone()[0]; table=store.snapshot_table(seq); contribution_table=store.metric_table(seq,'contributions')
        day=" AND day='2026-10-13'" if period=='CURRENT' else ''
        valid='valid_from<=? AND (valid_to IS NULL OR valid_to>?)'
        sql=f"""WITH
        p AS MATERIALIZED (SELECT id,payload FROM {table} WHERE kind='policy_catalog' AND ticker=? AND {valid}),
        changes_by_policy AS MATERIALIZED (
          SELECT route,max(json_extract(payload,'$.type')='ADD') added,max(json_extract(payload,'$.type')='MODIFY') modified,max(json_extract(payload,'$.type')='RETIRE') retired
          FROM {table} WHERE kind='policy_change' AND ticker=? AND {valid} {day} GROUP BY route),
        stats_by_policy AS MATERIALIZED (
          SELECT json_extract(dimensions,'$.business_key') business_key,max(metric='policy_ar_hits') hit,max(metric='policy_executed') executed
          FROM {contribution_table} WHERE ticker=? AND metric IN ('policy_ar_hits','policy_executed') AND {valid} {day} AND cast(value as numeric)>0 GROUP BY business_key)
        SELECT count(*),coalesce(sum(ch.added),0),coalesce(sum(ch.modified),0),coalesce(sum(ch.retired),0),coalesce(sum(st.hit),0),coalesce(sum(st.executed),0)
        FROM p LEFT JOIN changes_by_policy ch ON ch.route=p.id LEFT JOIN stats_by_policy st ON st.business_key=p.id"""
        args=[ticker,seq,seq]*3
        start=time.perf_counter();db.set_progress_handler(lambda:int(time.perf_counter()-start>1.5),1000)
        try: result=db.execute(sql,args).fetchone();err=None
        except sqlite3.OperationalError as e:result=None;err=str(e)
        finally:db.set_progress_handler(None,0)
        out['measurements'].append({'ticker':ticker,'period':period,'ms':round((time.perf_counter()-start)*1000,1),'error':err,'result':result})
print(json.dumps(out,ensure_ascii=True))
