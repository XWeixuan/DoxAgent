"""Supplemental read-only database summaries with timezone-correct cutoffs."""
import json,sqlite3,sys
from datetime import UTC,datetime,timedelta
mode=sys.argv[1]
out={'observed_at':datetime.now(UTC).isoformat()}
p='/data/bus/bus.sqlite3' if mode=='bus' else '/site-data/registry/site_strategy.sqlite3'
db=sqlite3.connect('file:'+p+'?mode=ro',uri=True,timeout=5);db.row_factory=sqlite3.Row
db.execute('pragma query_only=on')
def rows(sql,args=()):return [dict(r) for r in db.execute(sql,args)]
if mode=='bus':
 out['sources']=[{k:v.get(k) for k in ('source_id','default_parameters','entry_url','acquisition_mode')} for r in db.execute("select data_json from source_definitions where source_id in ('digitimes_tw_rss','huggingnews_rss','globenewswire_semiconductors_rss','yahoo_finance_news')") for v in [json.loads(r[0])]]
 out['runs']=rows("select source_id,status,count(*) n,min(created_at) first,max(created_at) last from distribution_runs where status!='DONE' group by 1,2")
 out['outbox']=rows('select status,count(*) n from site_strategy_result_outbox group by status')
else:
 cutoff=(datetime.now(UTC)-timedelta(days=2)).isoformat()
 out['body_48h_exact']=rows("select final_site_id,outcome,reason,count(*) n,max(completed_at) last from body_outcomes where julianday(completed_at)>=julianday(?) group by 1,2,3",(cutoff,))
 sites=('barrons','reuters','investing','investorshub','digitimes')
 out['last_real_attempts']={s:rows("select occurred_at,category,combination_id,payload_json from access_events where site_id=? and category in ('ACCESS_SUCCEEDED','ACCESS_FAILED','PURPOSE_ACCESS_DENIED','COMBINATION_RISK_FAILURE','COMBINATION_RECOVERY_PROBE') order by occurred_at desc limit 6",(s,)) for s in sites}
 out['identity_bindings']=[{k:v.get(k) for k in ('identity_id','runtime_kind','profile_id','egress_id')} for r in db.execute('select r.spec_json from browser_identity_revisions r join browser_identity_heads h on r.identity_id=h.identity_id and r.revision=h.active_revision') for v in [json.loads(r[0])]]
db.close();print(json.dumps(out,ensure_ascii=False),flush=True)
