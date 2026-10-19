"""Narrow audited control-plane update; retain URLs, schedule and stream policy."""
import json
from doxagent.settings import DoxAgentSettings
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.message_bus_v2.schema import UpdateActor

s=DoxAgentSettings();repo=MessageBusV2Repository(s.message_bus_v2_sqlite_path)
bus=MessageBusV2Service(repo)
current=repo.get_binding('CEG:newswire_rss')
assert current and current.enabled
parameters={**current.source_parameters,'http_routes':['de-standard-1']}
if parameters != current.source_parameters:
    updated=bus.update_binding(current.binding_id,{'source_parameters':parameters},
        actor=UpdateActor.SYSTEM,reason='20261019 direct ConnectTimeout; approved DE route independently returned 200 XML')
else:
    updated=current
assert updated.polling == current.polling and updated.streaming == current.streaming
assert updated.source_parameters['rss_urls']==current.source_parameters['rss_urls']
print(json.dumps({'binding_id':updated.binding_id,'version_before':current.version,'version_after':updated.version,
                  'http_routes':updated.source_parameters['http_routes'],'poll_interval':updated.polling.target_interval_seconds}))
