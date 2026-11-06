import json
from doxagent.message_bus_v2.collection_plane.observability import binding_states
ids=set(json.load(open('/data/diagnostics/rss-http-race-20261107/configuration-before.private.json'))['bindings'])
states=binding_states()
print(json.dumps({'enabled':states is not None,'bindings':{k:{'status':v['status'],'error':v['last_error_code'],'race_units':len(v['checkpoint'].get('race_channels',[]))} for k,v in (states or {}).items() if k in ids}}))
