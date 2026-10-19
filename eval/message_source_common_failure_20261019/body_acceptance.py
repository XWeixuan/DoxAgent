"""Read recent durable intake/outcome metadata without publishing or exposing bodies."""
import json, sqlite3
from doxagent.settings import DoxAgentSettings
from doxagent.v2_read.native_content import NativeContent

s=DoxAgentSettings()
path=s.message_bus_v2_sqlite_path
codec=NativeContent(path)
d=sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True,timeout=2)
out={'recent_raw':[], 'recent_articles':[]}
for collected,raw in d.execute('SELECT collected_at,data_json FROM raw_messages ORDER BY rowid DESC LIMIT 40'):
    item=json.loads(codec.text(raw));m=item.get('metadata',{}).get('media_enrichment',{})
    out['recent_raw'].append(dict(collected_at=collected,ticker=item.get('ticker'),source_id=item.get('source_id'),
        published_at=item.get('published_at'),outcome=m.get('outcome'),succeeded=m.get('succeeded'),
        reason=m.get('reason_code') or m.get('reason'),body_chars=len(item.get('body') or '')))
for source,state,raw in d.execute('SELECT source_id,body_state,enriched_json FROM distribution_articles ORDER BY rowid DESC LIMIT 20'):
    item=json.loads(codec.text(raw)) if raw else {};m=item.get('metadata',{}).get('media_enrichment',{})
    out['recent_articles'].append(dict(source_id=source,body_state=state,outcome=m.get('outcome'),reason=m.get('reason_code'),body_chars=len(item.get('body') or '')))
print(json.dumps(out))
