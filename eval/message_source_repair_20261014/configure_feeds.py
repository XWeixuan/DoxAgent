"""Apply only verified feed options through audited source updates."""
import copy
import json

from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.schema import UpdateActor
from doxagent.message_bus_v2.service import MessageBusV2Service

repo = MessageBusV2Repository('/data/bus/bus.sqlite3')
service = MessageBusV2Service(repo)
for source_id, key, value, rule in [
    ('globenewswire_semiconductors_rss', 'feed_browser_fallback', True,
     {'type': 'boolean'}),
    ('huggingnews_rss', 'feed_http_routes', ['de-standard-1', 'tr-standard-1'],
     {'type': 'array', 'minItems': 1, 'maxItems': 2,
      'items': {'type': 'string', 'enum': ['de-standard-1', 'tr-standard-1']}}),
]:
    source = service.require_source(source_id)
    parameters = dict(source.default_parameters)
    schema = copy.deepcopy(source.parameter_schema)
    parameters[key] = value
    schema.setdefault('properties', {})[key] = rule
    if parameters == source.default_parameters and schema == source.parameter_schema:
        print(json.dumps({'source': source_id, 'version': source.version, 'unchanged': True}))
        continue
    updated = service.update_source(
        source_id, {'default_parameters': parameters, 'parameter_schema': schema},
        actor=UpdateActor.SYSTEM,
        reason='20261014 verified bounded feed recovery; preserve identity and polling policy',
    )
    print(json.dumps({'source': source_id, 'version': updated.version}))
