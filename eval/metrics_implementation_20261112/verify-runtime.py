import json,subprocess
CODE='''import os,json,time
from datetime import datetime,UTC
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.calendar import PageCalendar
from doxagent.v2_read.query_budget import deadline
from doxagent.api_v2.bus_dashboard import dashboard_data
from doxagent.api_v2.runtime import runtime_metrics_data
from doxagent.api_v2.dto import validate
store=ReadStore(os.environ['DOXAGENT_V2_READ_SQLITE_PATH']);now=datetime.now(UTC);cal=PageCalendar();out={'at':now.isoformat(),'tickers':[]}
for ticker in ['RKLB','NBIS']:
 token=deadline.set(time.monotonic()+5);beg=time.monotonic()
 try:
  with store.connect() as db:seq=store.highwater(db)
  view={'seq':seq,'as_of':now.isoformat(),'wire':{'ticker':ticker,'page':'MESSAGE_BUS','clock':cal.clock(now,maintenance_pending=False),'period':cal.period('CURRENT_TRADING_DAY',now,semantic=True)}}
  bus=dashboard_data(store,ticker,view,{})
  validate('BusDashboard',bus)
  metrics=runtime_metrics_data(store,ticker,view);validate('RuntimeMetrics',metrics)
  with store.connect() as db:
   latest=[tuple(r) for r in db.execute("SELECT id,json_extract(payload,'$.w3_model_seconds'),json_extract(payload,'$.non_model_seconds'),json_extract(payload,'$.runtime_tags') FROM object_current INDEXED BY current_day WHERE kind='case' AND ticker=? AND day=? ORDER BY sort_key DESC LIMIT 8",(ticker,view['wire']['clock']['semantic_day']))]
  out['tickers'].append({'ticker':ticker,'seconds':time.monotonic()-beg,'bound_sources':bus['counts']['total'],'body':bus['metrics']['new_article_body'],'e2e_samples':bus['e2e_samples'],'capture_samples':bus['capture_samples'],'capture_mean':bus['mean_capture_seconds'],'cards_with_samples':sum(s['recent']['article_samples']>0 for s in bus['sources']),'runtime':{k:metrics[k]['current'] for k in ['hot_path_mean_seconds','w3_mean_seconds','related_cases','long_w3_cases','non_model_mean_seconds']},'latest':latest})
 except Exception as exc:out['tickers'].append({'ticker':ticker,'error':type(exc).__name__+': '+str(exc)})
 finally:deadline.reset(token)
print(json.dumps(out))
'''
out={}
for role in ['v2-api','v2-projector','v2-scheduler','codex-worker','v2-web']:
 ids=subprocess.check_output(['docker','ps','-q','--filter','label=com.docker.compose.service='+role],text=True).split()
 if not ids:continue
 cid=ids[0];d=json.loads(subprocess.check_output(['docker','inspect',cid],text=True))[0]
 im=json.loads(subprocess.check_output(['docker','image','inspect',d['Image']],text=True))[0]
 out[role]={'revision':im['Config'].get('Labels',{}).get('org.opencontainers.image.revision'),'state':d['State']['Status'],'health':d['State'].get('Health',{}).get('Status'),'restarts':d['RestartCount']}
 if role=='v2-api':
  p=subprocess.run(['docker','exec','-i',cid,'python','-'],input=CODE,text=True,capture_output=True,timeout=25)
  out[role]['probe']=json.loads(p.stdout) if not p.returncode else {'error':p.stderr[-1500:]}
print(json.dumps(out))
