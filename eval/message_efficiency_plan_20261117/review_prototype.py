"""Reuse reviewed evidence only; unmatched candidates remain unverified."""
import collections, json
from pathlib import Path

OUT=Path(__file__).resolve().parent
PRIOR=OUT.parent/'message_efficiency_followup_20261117'
idx=json.loads((PRIOR/'message_index.json').read_text(encoding='utf-8'))
lookup={r['id']:r for r in idx}
ledger=json.loads((PRIOR/'adjudication_ledger.json').read_text(encoding='utf-8'))
edges={frozenset((r['a_id'],r['b_id'])):r for r in ledger}
parent={}
def find(k):
    parent.setdefault(k,k)
    if parent[k]!=k:parent[k]=find(parent[k])
    return parent[k]
for e in ledger:
    if e['verdict']=='DUPLICATE_NO_NEW_ARTICLE_FACT':parent[find(e['b_id'])]=find(e['a_id'])
counts=collections.Counter();details=[]
for d in json.loads((OUT/'gate-prototype.json').read_text(encoding='utf-8'))['runs'][0]['decisions']:
    edge=edges.get(frozenset((d['drop'],d['keep'])))
    if edge:verdict=edge['verdict']
    elif find(d['keep'])==find(d['drop']):verdict='DUPLICATE_CONFIRMED_BY_REVIEWED_CHAIN'
    elif lookup[d['drop']]['title'].startswith(('Form 4 ','Form 144 ')):verdict='UNVERIFIED_FILING_IDENTITY'
    else:verdict='UNVERIFIED'
    counts[verdict]+=1
    details.append({**d,'verdict':verdict,'title':lookup[d['drop']]['title'],
        'source':lookup[d['drop']]['source_id'],'keep_source':lookup[d['keep']]['source_id'],
        'note':edge['note'] if edge else None})
result={'review_counts':dict(counts),'decisions':details}
(OUT/'prototype-review.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'counts':dict(counts),'known_risks':[d for d in details if d['verdict'] in ('FACT_CHANGED','ENRICHMENT','UNVERIFIED_FILING_IDENTITY')], 'unverified':[d for d in details if d['verdict']=='UNVERIFIED']},ensure_ascii=False,indent=2))
