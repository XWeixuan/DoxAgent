"""Idempotent registration of the approved five global test sources; no ticker binds."""
import json
from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.message_bus_v2.newswire_sources import initial_sources
from doxagent.message_bus_v2.schema import UpdateActor


def main():
    repo=MessageBusV2Repository(DoxAgentSettings().message_bus_v2_sqlite_path,initialize=False)
    bus=MessageBusV2Service(repo,enrichment_queue_enabled=True)
    actions=[]
    for source in initial_sources():
        current=repo.get_source(source.source_id)
        if current is None:
            bus.register_source(source.model_copy(update={'updated_reason':'User approved Massive Distribution isolated test rollout'}))
            action='registered_test'
        elif current.delivery_mode!='test':
            raise ValueError('Refusing to change a production source into test mode: '+source.source_id)
        elif not current.enabled:
            bus.update_source(source.source_id,{'enabled':True},actor=UpdateActor.USER,reason='User approved test source startup')
            action='enabled_test'
        else:action='unchanged'
        actions.append(dict(source_id=source.source_id,action=action))
    print(json.dumps(actions))


if __name__=='__main__':main()
