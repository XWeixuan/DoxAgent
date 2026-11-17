import json
from pathlib import Path
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.repository import InitializationRepository
from doxagent.event_library.provider import PublishedEventLibraryReader
s=DoxAgentSettings();r=InitializationRepository(s.ticker_initialization_control_path)
for t,d,count,gaps in [('ceg','20261110',52,32),('rklb','20261111',102,27)]:
 root=Path('/data/ops/'+t+'-refresh-'+d);a=json.loads((root/'activation.json').read_text());rid=a['run']['initialization_id'];x=r.get(rid);print(t,x.status,x.error,flush=True)
 if x.status!='SUCCEEDED':continue
 active=r.active_revision(t.upper());assert active['artifacts']==a['target_artifacts'];assert all(active['artifacts'][k]==a['before']['artifacts'][k] for k in ['event_library','monitoring_configuration'])
 nodes=[{'key':n.key,'status':n.status,'error':n.error} for n in r.nodes(rid,top_level_only=True)];assert len(nodes)==4 and all(n['status']=='SUCCEEDED' for n in nodes)
 with r._connection() as c:acks=[dict(v) for v in c.execute('select * from activation_worker_ack where ticker=? and revision_id=?',(t.upper(),active['revision_id']))]
 assert {'bus','runtime'} <= {v['worker'] for v in acks}
 e=active['artifacts']['event_library'];ev=PublishedEventLibraryReader(e['root']).reference_view(t.upper(),version=e['version'])
 audit={'ticker':t,'activation':x.model_dump(mode='json'),'nodes':nodes,'active':active,'ack':acks,'event_hash':ev.sha256,'policy_count':count,'coverage':'PARTIAL','gapcount':gaps,'event_and_monitoring_preserved':True};(root/'acceptance-final.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2));print(json.dumps(audit))
