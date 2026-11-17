"""Render candidate differences for manual adjudication; no verdicts automated."""
import collections
import hashlib
import html
import json
import re
import unicodedata
from pathlib import Path

from rapidfuzz.distance import Indel

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
INPUT = ROOT / '.tmp-codex/message_efficiency_followup_20261117/published_content.jsonl'

def norm(s):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',html.unescape(s or ''))).strip().casefold()

lines=[json.loads(s) for s in INPUT.read_text(encoding='utf-8').splitlines()][1:]
rows=sorted(lines,key=lambda r:(r['ticker'],r['stream_published_at'],r['id']))
edges={(r['i'],r['j']):r for r in json.loads((OUT/'candidates.json').read_text(encoding='utf-8'))}
exp=json.loads((OUT/'experiment.json').read_text(encoding='utf-8'))
result=[]
for serial,d in enumerate(exp['schemes']['url_or_title95_or_body95']['decisions']):
    i,j=d['keep'],d['drop'];edge=edges[tuple(sorted((i,j)))];a,b=rows[i],rows[j]
    ba,bb=norm(a['body']),norm(b['body']);diffs=[]
    for op in Indel.opcodes(ba,bb):
        if op.tag=='equal':continue
        diffs.append({'type':op.tag,'a_start':op.src_start,'b_start':op.dest_start,'a':ba[max(0,op.src_start-30):min(len(ba),op.src_end+30)][:700],'b':bb[max(0,op.dest_start-30):min(len(bb),op.dest_end+30)][:700]})
    result.append({'serial':serial,'keep':i,'drop':j,'edge':edge,'diffs':diffs,'body_head_a':ba[:700],'body_head_b':bb[:700]})
(OUT/'candidate_review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print('candidate review rows',len(result))
for r in result:
    e=r['edge'];print(r['serial'],r['keep'],r['drop'],e['a']['ticker'],','.join(e['schemes']),round(e['body_score'] or 0,2),'|',e['a']['title'],'<>',e['b']['title'])
