"""Integrity-only native regex replay, with no GOLD or tuning access."""
import collections,hashlib,inspect,json,sys
from pathlib import Path
P=Path(__file__).parent;ROOT=P.parents[1]
sys.path.insert(0,str(ROOT/'src'))
from doxagent.message_bus_v2.monitoring_terms import TickerMonitoringTerms
from doxagent.message_bus_v2.relevance import regex_relevant,_match,_normalize
from doxagent.message_bus_v2.schema import RawMessageInput
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def rows(p):
 with p.open(encoding='utf-8-sig') as f:return [json.loads(s) for s in f if s.strip()]
inv=read(P/'inventory.json');terms={t:TickerMonitoringTerms.model_validate(v) for t,v in inv['terms'].items()}
functions={f.__name__:hashlib.sha256(inspect.getsource(f).encode()).hexdigest() for f in [regex_relevant,_match,_normalize]}
assert functions==inv['classification_function_sha256'],'Native classifier differs from production snapshot'
results=[]
for split in ['development','validation']:
 for r in rows(P/'sets'/f'{split}-unlabeled.jsonl'):
  message=RawMessageInput(**r['evidence'],url=r['canonical_url'],raw_payload={},metadata={'language':r['language']})
  hit=regex_relevant(terms[r['ticker']],r['language'],message)
  results.append({'pair_id':r['pair_id'],'split':split,'ticker':r['ticker'],'language':r['language'],'prediction':hit,'terms_revision':r['terms_revision'],'evidence_sha256':r['evidence_sha256']})
assert all(r['prediction'] for r in results),'Sample must belong to frozen current-regex recall pool'
(P/'baseline-integrity-predictions.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in results),encoding='utf-8')
receipt={'article_ticker_pairs':len(results),'replay_positive':sum(r['prediction'] for r in results),'mismatches':sum(not r['prediction'] for r in results),'function_sha256':functions,'counts':{s:dict(collections.Counter(r['ticker'] for r in results if r['split']==s)) for s in ['development','validation']},'purpose':'Pool membership integrity only; no GOLD scoring and no candidate tuning.'}
(P/'baseline-integrity.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8');print(json.dumps(receipt))
