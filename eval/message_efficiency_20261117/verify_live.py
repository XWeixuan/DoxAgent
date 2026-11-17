"""Final stream and Massive BODY-gate checks, read-only."""
import base64
import hashlib
import inspect
import json
import os
import sqlite3
from types import SimpleNamespace

from doxagent.message_bus_v2.distribution_repository import _complete_body_verified
from doxagent.v2_read.native_content import NativeContent

targets = json.loads(base64.b64decode('__IDS__'))
ids = [r['id'] for r in targets]
p = os.environ['DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH']
c = sqlite3.connect('file:' + p + '?mode=ro', uri=True, timeout=2)
c.execute('pragma query_only=on')
codec = NativeContent(p)
counts = c.execute('select count(*),count(distinct m.standard_message_id),count(distinct m.stream_item_id) from json_each(?) j join stream_members m on m.standard_message_id=j.value', (json.dumps(ids),)).fetchone()
out = {'stream_counts': counts, 'massive_body_violations': [], 'massive_body_verified': 0, 'hashes': {}}
for target in targets:
    if not target['source_id'].endswith('_massive'):
        continue
    d = codec.decode(json.loads(c.execute('select data_json from standard_messages where standard_message_id=?', (target['id'],)).fetchone()[0]))
    msg = SimpleNamespace(body=d.get('body'), metadata=d.get('metadata', {}))
    if _complete_body_verified(msg):
        out['massive_body_verified'] += 1
    else:
        out['massive_body_violations'].append({'id': target['id'], 'source_id':d['source_id'], 'outcome':msg.metadata.get('media_enrichment',{}).get('outcome')})
for module in ['doxagent.message_bus_v2.technology_body','doxagent.message_bus_v2.energy_body','doxagent.content_enrichment.quality','doxagent.message_bus_v2.live','doxagent.message_bus_v2.relevance','doxagent.message_bus_v2.deduplication','doxagent.message_bus_v2.body_results']:
    m = __import__(module, fromlist=['_'])
    out['hashes'][module] = hashlib.sha256(open(inspect.getfile(m),'rb').read()).hexdigest()
c.close()
print(json.dumps(out))
