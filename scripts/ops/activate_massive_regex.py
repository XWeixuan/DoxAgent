"""CAS merge nine Massive rule maps, then configure 45 real source bindings."""
import json
import os
from pathlib import Path
import sys

from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.message_bus_v2.monitoring_terms import MonitoringTermsService, TickerMonitoringTerms
from doxagent.message_bus_v2.schema import UpdateActor, utc_now

os.umask(0o077)
directory=Path(sys.argv[1])
directory.mkdir(exist_ok=True,parents=True)
rules=json.loads((directory/'terms.json').read_text())
ids=['businesswire_massive','prnewswire_massive','globenewswire_massive','access_newswire_massive','tmx_newsfile_massive']
repo=MessageBusV2Repository(Path('/data/bus/bus.sqlite3'))
bus=MessageBusV2Service(repo)
terms=MonitoringTermsService(repo)
backup=directory/'before.private.json'
if not backup.exists():
    backup.write_text(json.dumps({'at':utc_now().isoformat(),'sources':{s:repo.get_source(s).model_dump(mode='json') for s in ids},'terms':{t:terms.get(t)[1].model_dump(mode='json') for t in rules},'bindings':[b.model_dump(mode='json') for b in repo.list_bindings() if b.source_id in ids]},ensure_ascii=False))
receipt={'activation_at':utc_now().isoformat(),'terms':{},'sources':{},'bindings':[]}
for ticker,value in rules.items():
    revision,current=terms.get(ticker)
    config=current.model_dump(mode='json')
    before={k:v for k,v in config.items() if k not in {'massive_l2','expected_revision'}}
    config.update(expected_revision=revision,massive_l2=value['massive_l2'])
    new=TickerMonitoringTerms.model_validate(config)
    revision=terms.apply(new,actor='massive-regex-rollout-20261113')
    _,saved=terms.get(ticker)
    assert {k:v for k,v in saved.model_dump(mode='json').items() if k not in {'massive_l2','expected_revision'}} == before
    assert saved.massive_l2 == new.massive_l2
    receipt['terms'][ticker]=revision
for sid in ids:
    s=repo.get_source(sid)
    bus.update_source(sid,{'enabled':False},actor=UpdateActor.USER,reason='Massive isolated regex activation')
    s=repo.get_source(sid)
    policy=s.distribution_policy.model_copy(update={'test_tickers':[],'jev_enabled':False,'require_complete_body':True})
    bus.update_source(sid,{'delivery_mode':'production','default_parameters':{**s.default_parameters,'massive_regex_enabled':True},'distribution_policy':policy.model_dump(mode='json')},actor=UpdateActor.USER,reason='Massive production scope')
    for ticker in rules:
        b=bus.configure_binding(ticker=ticker,source_id=sid,actor=UpdateActor.USER,reason='Massive nine-ticker subscription',enabled=True)
        receipt['bindings'].append(b.binding_id)
    s=bus.update_source(sid,{'enabled':True},actor=UpdateActor.USER,reason='Enable isolated Massive production')
    receipt['sources'][sid]=s.version
assert len(receipt['bindings'])==45
(directory/'activation.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt))
