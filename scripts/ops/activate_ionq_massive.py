"""CAS-merge one Massive rule map and idempotently enable six real bindings."""
import hashlib,json,os,sys
from pathlib import Path
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService,TickerMonitoringTerms
from doxagent.message_bus_v2.schema import AcquisitionMode,UpdateActor,utc_now,canonical_json

os.umask(0o077)
candidate=TickerMonitoringTerms.model_validate_json(Path(sys.argv[1]).read_text())
assert candidate.ticker=='IONQ'
directory=Path(sys.argv[2]);directory.mkdir(parents=True,exist_ok=True);directory.chmod(0o700)
(directory/'best.json').write_text(candidate.model_dump_json(indent=2));(directory/'best.json').chmod(0o600)
repo=MessageBusV2Repository(Path('/data/bus/bus.sqlite3'));bus=MessageBusV2Service(repo);svc=MonitoringTermsService(repo)
sources=[s for s in repo.list_sources() if s.acquisition_mode is AcquisitionMode.MASSIVE_DISTRIBUTION]
assert {s.source_id for s in sources}=={'businesswire_massive','prnewswire_massive','globenewswire_massive','access_newswire_massive','tmx_newsfile_massive','bloomberg_massive'}
assert all(s.enabled and s.delivery_mode=='production' and not s.distribution_policy.jev_enabled and s.distribution_policy.require_complete_body for s in sources)
revision,current=svc.get('IONQ')
original=TickerMonitoringTerms.model_validate_json(Path(sys.argv[3]).read_text())
assert current.massive_l2==original.massive_l2 or current.massive_l2==candidate.massive_l2, 'Concurrent Massive terms changed; inspect before merging'
others=['INTC','MU','BE','COHR','NBIS','CEG','RKLB','SNOW','ONDS']
def digest(value):return hashlib.sha256(canonical_json(value).encode()).hexdigest()
other_hashes={t:digest(svc.get(t)[1].model_dump(mode='json')) for t in others}
backup=directory/'before.private.json'
if not backup.exists():
    backup.write_text(json.dumps({'at':utc_now().isoformat(),'revision':revision,'terms':current.model_dump(mode='json'),'bindings':[b.model_dump(mode='json') for b in repo.list_bindings(ticker='IONQ') if b.source_id in {s.source_id for s in sources}]},ensure_ascii=False))
backup.chmod(0o600)
ordinary={k:v for k,v in current.model_dump(mode='json').items() if k not in {'massive_l2','expected_revision'}}
if current.massive_l2!=candidate.massive_l2:
    merged=TickerMonitoringTerms.model_validate({**current.model_dump(mode='json'),'expected_revision':revision,'massive_l2':candidate.model_dump(mode='json')['massive_l2']})
    revision=svc.apply(merged,actor='IONQ-massive-regex-tuning-20261113')
receipt={'at':utc_now().isoformat(),'ticker':'IONQ','terms_revision':revision,'created_bindings':[],'existing_bindings_preserved':[]}
for source in sources:
    binding=repo.get_binding('IONQ:'+source.source_id)
    if binding and binding.enabled and binding.polling.enabled:
        receipt['existing_bindings_preserved'].append(binding.binding_id);continue
    bus.configure_binding(ticker='IONQ',source_id=source.source_id,enabled=True,actor=UpdateActor.USER,
        polling={**(binding.polling.model_dump(mode='json') if binding else source.default_polling_config.model_dump(mode='json')),'enabled':True},
        streaming=binding.streaming.model_dump(mode='json') if binding else None,
        reason='IONQ Massive GOLD-tuned independent regex activation')
    receipt['created_bindings'].append('IONQ:'+source.source_id)
_,saved=svc.get('IONQ')
assert saved.massive_l2==candidate.massive_l2
assert {k:v for k,v in saved.model_dump(mode='json').items() if k not in {'massive_l2','expected_revision'}}==ordinary
assert {t:digest(svc.get(t)[1].model_dump(mode='json')) for t in others}==other_hashes
receipt.update(ordinary_fields_unchanged=True,other_nine_terms_unchanged=True,massive_l2_sha256=digest(saved.model_dump(mode='json')['massive_l2']),bindings=[{'source_id':s.source_id,'binding_id':'IONQ:'+s.source_id,'enabled':repo.get_binding('IONQ:'+s.source_id).enabled,'polling_enabled':repo.get_binding('IONQ:'+s.source_id).polling.enabled} for s in sources])
assert len(receipt['bindings'])==6 and all(b['enabled'] and b['polling_enabled'] for b in receipt['bindings'])
(directory/'activation.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2))
print(json.dumps(receipt,ensure_ascii=False))
