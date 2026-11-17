"""Compare small, explicitly selected same-title pairs without exporting bodies."""
import base64
import difflib
import hashlib
import json
import os
import re
import sqlite3

from doxagent.v2_read.native_content import NativeContent

GROUPS = json.loads(base64.b64decode('__GROUPS__'))
p = os.environ['DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH']
c = sqlite3.connect('file:' + p + '?mode=ro', uri=True, timeout=2)
c.execute('pragma query_only=on')
codec = NativeContent(p)
out = []
for group in GROUPS:
    items = []
    bodies = []
    for item in group:
        raw = c.execute('select data_json from standard_messages where standard_message_id=?', (item['id'],)).fetchone()[0]
        d = codec.decode(json.loads(raw))
        body = re.sub(r'\s+', ' ', d.get('body') or '').strip()
        bodies.append(body)
        items.append({'id':item['id'],'ticker':d['ticker'],'source_id':d['source_id'],'title':d.get('title'),'url':d['url'],'body_chars':len(body),'body_head':body[:200], 'body_sha256':hashlib.sha256(body.encode()).hexdigest()})
    comparisons = []
    for i in range(1,len(items)):
        a,b = bodies[0],bodies[i]
        sa,sb = set(re.findall(r'\w+',a.casefold())),set(re.findall(r'\w+',b.casefold()))
        sm = difflib.SequenceMatcher(None,a,b,autojunk=False) if max(len(a),len(b))<40000 else None
        diff = []
        if sm:
            for tag,i1,i2,j1,j2 in sm.get_opcodes():
                if tag!='equal':
                    diff.append({'type':tag,'a':a[max(0,i1-40):min(len(a),i2+40)][:240],'b':b[max(0,j1-40):min(len(b),j2+40)][:240]})
                if len(diff)>=3:break
        comparisons.append({'against':items[i]['id'],'text_ratio':sm.ratio() if sm else None,'token_jaccard':len(sa&sb)/len(sa|sb) if sa|sb else None,'differences':diff})
    out.append({'items':items,'comparisons':comparisons})
c.close()
print(json.dumps(out,ensure_ascii=False))
