import collections, json, shutil, sqlite3, subprocess, sys, time
from datetime import datetime, UTC

ROOT='/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/'
SOURCES=['barrons_ticker_news','investing_ticker_news','investorshub_ticker_news']
SINCE=sys.argv[1] if len(sys.argv)>1 else '2026-11-12T21:14:09+00:00'
def db(path):
    c=sqlite3.connect('file:'+path+'?mode=ro',uri=True,timeout=2); c.row_factory=sqlite3.Row
    c.execute('pragma query_only=on'); start=time.monotonic()
    c.set_progress_handler(lambda:time.monotonic()-start>30,10000); return c
def run(args):return subprocess.check_output(args,text=True).strip()
b=db(ROOT+'bus.sqlite3'); a=db(ROOT+'acquisition.sqlite3')
out={'at':datetime.now(UTC).isoformat(),'since':SINCE,'sources':{}}
for source in SOURCES:
    cycles=[dict(r) for u in a.execute('select binding_id,unit from units where source_id=?',(source,)).fetchall()
            for r in a.execute('select ticker,unit,status,reason,dispatch_at,finished_at,interval_seconds from cycles indexed by cycles_unit_time where binding_id=? and unit=? and dispatch_at>=?',(u[0],u[1],SINCE))]
    articles=[dict(r) for r in b.execute('select article_key,title,first_seen_at,initial_result_at,outcome,last_error,execution_spent_ms from live_articles indexed by live_article_observed where first_seen_at>=? and source_id=?',(SINCE,source))]
    # The legacy Bus poll_states projection stopped updating on October 27;
    # current acquisition cycles, not that projection, are the live evidence.
    latest={}
    for row in sorted(cycles,key=lambda x:x['dispatch_at']):latest[row['ticker']+':'+row['unit']]=row
    out['sources'][source]={'cycles':dict(collections.Counter(str(r['status'])+':'+str(r['reason']) for r in cycles)),
        'tickers':{t:{'cycles':dict(collections.Counter(str(r['status'])+':'+str(r['reason']) for r in cycles if r['ticker']==t)),
                       'last_finished_at':max((r['finished_at'] or '' for r in cycles if r['ticker']==t),default=None)} for t in sorted({r['ticker'] for r in cycles})},
        'intervals':sorted({r['interval_seconds'] for r in cycles}),'articles':articles,'latest_cycles':latest}
r=db('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3')
out['auth']=[dict(x)|{'data_json':{k:json.loads(x['data_json']).get(k) for k in ['auth_state','checked_at','reason','body_state']}} for x in r.execute("select site_id,identity_id,data_json,updated_at from site_identity_auth where identity_id in ('dowjones-main','investorshub-main','investorshub-backup')")]
out['recovery']=[{k:v for k,v in dict(x).items() if k!='data_json'}|{'detail':{k:json.loads(x['data_json']).get(k) for k in ['handling_mode','reason_code','challenge_kind','blocked_since','target_id']}} for x in r.execute("select * from browser_recoveries where identity_id in ('investorshub-main','investorshub-backup')")]
out['tasks']=[{k:x[k] for k in ['task_id','identity_id','state','updated_at']} for x in r.execute("select * from challenge_tasks where identity_id in ('investorshub-main','investorshub-backup') and state <> 'RESOLVED' and superseded_by is null")]
out['body_config']={}
for site in ['investorshub','barrons']:
    v=r.execute('select s.revision,s.spec_json from site_strategy_revisions s join site_strategy_heads h on h.site_id=s.site_id and h.active_revision=s.revision where s.site_id=?',(site,)).fetchone()
    spec=json.loads(v['spec_json']);out['body_config'][site]={'revision':v['revision'],'parameters':spec.get('body',{}).get('parameters',{})}
out['containers']={}
for role in ['v2-site-access','v2-content-enrichment','v2-message-collector','v2-chrome-supervisor']:
    name='doxagent-v2-'+role+'-1';i=json.loads(run(['docker','inspect',name]))[0]
    v={'id':i['Id'],'started_at':i['State']['StartedAt'],'status':i['State']['Status'],'health':i['State'].get('Health',{}).get('Status')}
    v['recheck_env']=[x for x in i['Config'].get('Env',[]) if 'RECHECK' in x]
    files={'v2-site-access':['site_strategy/runtime.py','site_strategy/barrons_document.py'],
           'v2-content-enrichment':['content_enrichment/pipeline.py','content_enrichment/quality.py','content_enrichment/publisher_documents.py'],
           'v2-message-collector':['message_bus_v2/industry_sources.py']}.get(role,[])
    v['hashes']={f:run(['docker','exec',name,'python','-c',"import hashlib,doxagent,pathlib;print(hashlib.sha256((pathlib.Path(doxagent.__file__).parent/"+repr(f)+").read_bytes()).hexdigest())"]) for f in files}
    out['containers'][role]=v
out['root_free_gib']=round(shutil.disk_usage('/').free/2**30,2)
out['mem_available_kib']=next(int(x.split()[1]) for x in open('/proc/meminfo') if x.startswith('MemAvailable:'))
print(json.dumps(out,ensure_ascii=False))
