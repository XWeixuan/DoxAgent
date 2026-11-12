"""Public readback and fresh delivery facts after the IONQ-only activation."""
import hashlib,json,sqlite3
from pathlib import Path
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService,TickerMonitoringTerms
from doxagent.v2_control.monitoring_terms import MonitoringTermsControl
from doxagent.api_v2.dto import validate
from doxagent.message_bus_v2.schema import utc_now,canonical_json
from doxagent.settings import DoxAgentSettings
from doxagent.persistent_runtime_v2.journal import RuntimeJournal

r=MessageBusV2Repository(Path('/data/bus/bus.sqlite3'))
rev,t=MonitoringTermsService(r).get('IONQ')
candidate=TickerMonitoringTerms.model_validate_json(Path('/data/bus/ops/ionq-massive-20261113/best.json').read_text())
assert t.massive_l2==candidate.massive_l2
snapshot=MonitoringTermsControl(r.path,initialize=False).get_terms('IONQ');validate('MonitoringTermsConfig',snapshot)
consumers=[x for x in snapshot['consumers'] if x['acquisition_mode']=='massive_distribution']
assert len(consumers)==6 and all(x['source_enabled'] and x['binding_enabled'] for x in consumers)
activation=json.loads(Path('/data/bus/ops/ionq-massive-20261113/activation.json').read_text());start=activation['at']
before=json.loads(Path('/data/bus/ops/ionq-massive-20261113/before.private.json').read_text())['terms']
assert {k:v for k,v in before.items() if k not in {'massive_l2','expected_revision'}}=={k:v for k,v in t.model_dump(mode='json').items() if k not in {'massive_l2','expected_revision'}}
ids=[x['source_id'] for x in consumers]
out={'at':utc_now().isoformat(),'activation_at':start,'terms_revision':rev,'schema_valid':True,'consumer_count':len(consumers),'massive_required_languages':snapshot['massive_required_languages'],'massive_l2_sha256':hashlib.sha256(canonical_json(t.model_dump(mode='json')['massive_l2']).encode()).hexdigest(),'consumers':[{'source_id':x['source_id'],'enabled':x['source_enabled'] and x['binding_enabled']} for x in consumers]}
out['engine_sha256']={n:hashlib.sha256(Path('/app/src/doxagent/message_bus_v2/'+n+'.py').read_bytes()).hexdigest() for n in ['massive_relevance','relevance','monitoring_terms']}
out['engine_text_sha256']={n:hashlib.sha256(Path('/app/src/doxagent/message_bus_v2/'+n+'.py').read_text().encode()).hexdigest() for n in ['massive_relevance','relevance','monitoring_terms']}
with r._connect() as db:
    out['new_deliveries']=[dict(x) for x in db.execute("SELECT source_id,state,regex_result,jev_result,count(*) n FROM live_deliveries WHERE ticker='IONQ' AND source_id IN (SELECT value FROM json_each(?)) AND created_at>=? GROUP BY source_id,state,regex_result,jev_result",(json.dumps(ids),start))]
    out['publications']=[dict(x) for x in db.execute("SELECT d.source_id,d.standard_id,json_extract(raw.data_json,'$.title') title,json_extract(raw.data_json,'$.url') url,json_extract(raw.data_json,'$.published_at') published_at,json_extract(raw.data_json,'$.collected_at') collected_at,json_extract(raw.data_json,'$.metadata.relevance_filter') relevance_filter,json_extract(raw.data_json,'$.metadata.relevance_terms_revision') relevance_revision FROM live_deliveries d JOIN raw_messages raw ON raw.raw_message_id=d.raw_id WHERE d.ticker='IONQ' AND d.source_id IN (SELECT value FROM json_each(?)) AND d.created_at>=? AND d.state='PUBLISHED' LIMIT 15",(json.dumps(ids),start))]
with sqlite3.connect('file:/data/bus/acquisition.sqlite3?mode=ro',uri=True,timeout=5) as db:
    out['listing_units']=[{'source_id':s,'unit':u,**{k:json.loads(head).get(k) for k in ['last_capture_at','last_attempt_at','status','last_error_code']}} for s,u,head in db.execute("SELECT DISTINCT source_id,unit,head_json FROM units WHERE ticker='IONQ' AND source_id IN (SELECT value FROM json_each(?)) AND unit NOT LIKE 'TEST:%'",(json.dumps(ids),))]
settings=DoxAgentSettings();journal=RuntimeJournal(settings.persistent_runtime_v2_sqlite_path,initialize=False)
for item in out['publications']:
    task=journal.get_task('inbox:IONQ:'+item['standard_id'])
    item['inbox_status']=task.get('status') if task else None
assert all(x['jev_result'] is None for x in out['new_deliveries'])
print(json.dumps(out,ensure_ascii=False))
