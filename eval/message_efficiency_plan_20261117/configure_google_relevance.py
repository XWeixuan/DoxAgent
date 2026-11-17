"""Enable the existing Google relevance switch only; no subscriptions or queries added."""
import argparse
import hashlib
import json
import os
from datetime import UTC, datetime

from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import UpdateActor
from doxagent.message_bus_v2.service import MessageBusV2Service

ap=argparse.ArgumentParser()
ap.add_argument('--apply',action='store_true')
args=ap.parse_args()
p=os.environ['DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH']
repo=MessageBusV2Repository(p,initialize=False)
service=MessageBusV2Service(repo)
source=repo.get_source('google_news_search_rss')
assert source.acquisition_mode.value=='by_search'
assert source.parameter_schema['properties']['require_relevance']['type']=='boolean'
out={'at':datetime.now(UTC).isoformat(),'applied':args.apply,'bindings':[]}
with repo.atomic_intake():
    bindings=repo.list_bindings(source_id=source.source_id)
    # Preserve the existing explicit MU exclusion; do not create or enable bindings.
    active=[b for b in bindings if b.enabled and b.polling.enabled]
    assert len(active)==9 and all(b.ticker!='MU' for b in active)
    for b in active:
        before=b.model_dump(mode='json')
        changed=b.source_parameters.get('require_relevance') is not True
        if args.apply and changed:
            b=service.update_binding(b.binding_id,
                {'source_parameters':{**b.source_parameters,'require_relevance':True}},
                actor=UpdateActor.USER,reason='Requested search recall followed by existing distribution relevance filtering')
        after=b.model_dump(mode='json')
        ignored={'source_parameters','version','updated_at','updated_by','updated_reason'}
        assert {k:v for k,v in before.items() if k not in ignored}=={k:v for k,v in after.items() if k not in ignored}
        assert {k:v for k,v in before['source_parameters'].items() if k!='require_relevance'}=={k:v for k,v in after['source_parameters'].items() if k!='require_relevance'}
        if args.apply:assert after['source_parameters']['require_relevance'] is True
        out['bindings'].append({'binding_id':b.binding_id,'changed':changed,'before':before,'after':after})
    assert repo.get_source(source.source_id).model_dump(mode='json')==source.model_dump(mode='json')
repo.close()
print(json.dumps(out,ensure_ascii=False))
