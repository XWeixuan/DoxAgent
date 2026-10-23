import json,sqlite3,subprocess
from pathlib import Path
from datetime import datetime,UTC
root=Path('/var/lib/doxagent/defense-sources-20261024');db=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/bus/bus.sqlite3');out={'at':datetime.now(UTC).isoformat()}
with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as d:
 sources={key:json.loads(raw) for key,raw in d.execute('SELECT source_id,data_json FROM source_definitions') if json.loads(raw).get('adapter_ref')=='builtin:defense_public@1'}
 out['source_count']=len(sources);out['bindings']=[]
 for key,source_id,raw in d.execute("SELECT binding_id,source_id,data_json FROM ticker_source_bindings WHERE ticker IN ('RKLB','ONDS')"):
  if source_id not in sources:continue
  x=json.loads(raw);p=d.execute('SELECT data_json FROM poll_states WHERE binding_id=?',(key,)).fetchone();p=json.loads(p[0]) if p else {}
  out['bindings'].append({'binding_id':key,'enabled':x['enabled'],'polling_enabled':x['polling']['enabled'],'interval':x['polling']['target_interval_seconds'],'poll':{k:p.get(k) for k in ['status','last_attempt_at','last_success_at','last_error_code','last_error_message','last_latency_ms']}})
 out['terms']=d.execute("SELECT ticker,current_revision FROM ticker_monitoring_terms WHERE ticker IN ('RKLB','ONDS')").fetchall()
 out['new_jobs']=d.execute("SELECT status,COUNT(*) FROM content_enrichment_jobs WHERE json_extract(data_json,'$.source.adapter_ref')='builtin:defense_public@1' GROUP BY status").fetchall()
 out['new_body_results']=d.execute("SELECT status,COUNT(*) FROM article_body_results WHERE json_extract(data_json,'$.source_id') IN ("+','.join('?' for s in sources)+") GROUP BY status",tuple(sources)).fetchall()
rows=[]
for name in ['v2-content-enrichment','v2-message-bus','v2-scheduler','v2-api','v2-chrome-supervisor']:
 x=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-'+name+'-1']))[0];rows.append({'component':name,'container':x['Id'],'image':x['Image'],'state':x['State']['Status'],'health':x['State'].get('Health',{}).get('Status'),'restarts':x['RestartCount'],'started_at':x['State']['StartedAt']})
out['containers']=rows;before=json.loads((root/'running-before.json').read_text());old=next(x for x in before if x['component']=='v2-chrome-supervisor');new=rows[-1];out['supervisor_preserved']=old['container']==new['container'] and old['started_at']==new['started_at'];out['timer_active']=subprocess.run(['systemctl','is-active','--quiet','doxagent-release-resources.timer']).returncode==0
(root/'status.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print(json.dumps(out,ensure_ascii=False))
