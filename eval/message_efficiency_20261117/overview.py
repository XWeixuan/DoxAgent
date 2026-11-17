import sqlite3,json,os,time
out={}
for label,p in [('bus',os.environ['DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH']),('read',os.environ['DOXAGENT_V2_READ_SQLITE_PATH'])]:
 c=sqlite3.connect('file:'+p+'?mode=ro',uri=True,timeout=2);c.row_factory=sqlite3.Row;c.execute('pragma query_only=on');start=time.monotonic();c.set_progress_handler(lambda: int(time.monotonic()-start>15),10000)
 out[label]={'indexes':[dict(r) for r in c.execute("select name,tbl_name,sql from sqlite_master where type='index' and tbl_name in ('stream_items','stream_members','standard_messages','raw_messages','live_deliveries','live_origins','live_articles','metric_buckets_current','contributions_current','object_current')")]}
 if label=='read':
  out[label]['days']=[dict(r) for r in c.execute("select metric,day,sum(cast(value as real)) n from metric_buckets_current where metric in ('messages','bus_messages','processed_cases') and day>='2026-10-19' and day<='2026-11-16' group by metric,day order by day")]
  out[label]['sample']=[dict(r) for r in c.execute("select * from object_current where kind='message' and ticker='MU' and day='2026-11-16' limit 1")]
 else:
  out[label]['sources']=[{'source_id':r['source_id'],'version':r['version'],'enabled':r['enabled'],**{k:v for k,v in json.loads(r['data_json']).items() if k in ('name','kind','acquisition_mode','language','distribution_policy','created_at','updated_at')}} for r in c.execute('select * from source_definitions')]
  out[label]['bindings']=[dict(r) for r in c.execute('select ticker,source_id,enabled,tombstoned_at,version from ticker_source_bindings where tombstoned_at is null')]
 c.close()
print(json.dumps(out,ensure_ascii=False))
