"""Export only explicitly supplied published messages over a read-only connection."""
import base64
import json
import os
import sqlite3
from datetime import UTC, datetime

from doxagent.v2_read.native_content import NativeContent

targets = json.loads(base64.b64decode('__TARGETS__'))
p = os.environ['DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH']
c = sqlite3.connect('file:' + p + '?mode=ro', uri=True, timeout=2)
c.execute('pragma query_only=on')
codec = NativeContent(p)
print(json.dumps({'exported_at':datetime.now(UTC).isoformat(), 'kind':'manifest'}))
for item in targets:
    row = c.execute('select data_json from standard_messages where standard_message_id=?', (item['id'],)).fetchone()
    d = codec.decode(json.loads(row[0]))
    md = d.get('metadata', {})
    ce = md.get('content_evidence', {})
    print(json.dumps({**item, 'title':d.get('title') or '', 'body':d.get('body') or '',
                      'summary':ce.get('summary') or '', 'url':d['url'], 'language':md.get('language') or '',
                      'body_kind':ce.get('kind'), 'outcome':md.get('media_enrichment',{}).get('outcome'),
                      'published_at':d.get('published_at'), 'source_name':d.get('source_name'),
                      'identity_evidence':md.get('identity_evidence') or {},
                      'article_key':md.get('live_article_key'),
                      'provider':md.get('provider')},ensure_ascii=False))
c.close()
