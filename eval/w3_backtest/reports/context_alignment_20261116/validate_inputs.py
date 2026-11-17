import json,collections,hashlib
from pathlib import Path
from datetime import datetime
from doxagent.persistent_runtime_v2.w3_research_store import W3ResearchStore
from doxagent.persistent_runtime_v2.w3_v6 import scoped_manifest
from doxagent.w3_backtest.dataset import read_json,write_json
from doxagent.w3_backtest.context import read_context,visible_input,execution_profile,validate_market,coverage
from doxagent.w3_backtest.decision_runner import runtime_case
root=Path('eval/w3_backtest/datasets/w3_curated_20261030');out=Path('eval/w3_backtest/reports/context_alignment_20261116');store=W3ResearchStore(Path('D:/DoxAgentBacktest/input_alignment_20261116'));store.initialize();rows=[];issues=[]
for entry in read_json(root/'manifest.json')['cases']:
 raw=read_json(root/entry['path']);key=raw['case_key'];ctx=read_context(root,key);profile=execution_profile(raw,ctx);case=runtime_case(raw,out,{},profile);visible=visible_input(raw,case,ctx)
 f=root/'inputs'/key/'evidence';market=read_json(f/'market_view.json') if (f/'market_view.json').exists() else None;validate_market(market,raw)
 evidence={p.relative_to(f).as_posix():p.read_text(encoding='utf-8') for p in f.rglob('*') if p.is_file() and p.suffix in {'.md','.json','.jsonl'} and p.name!='market_view.json'}
 research=root/'research'/raw['research_key'];components=store.install({n:p.read_text(encoding='utf-8-sig') for n,p in [('d1',research/'document1.md'),('d2',research/'document2.json')] if p.exists()},raw['ticker'])
 manifest=scoped_manifest(dict(mode='OFFLINE_REPLAY',ticker=raw['ticker'],case_id=case.case_id,phase='trade_decision',w3_contract_version=case.w3_contract_version,cutoff_at=raw['as_of'],components=components,evidence=evidence,market_view=market))
 ref=store.bind(case.case_id,'trade_decision',manifest)['context_ref'];store.call('catalog',dict(context_ref=ref),'d1-d2.v1')
 for obj in [dict(kind='policy',id='p'),dict(kind='raw_document',id='policy'),dict(kind='reference_event',id='E1')]:
  try:store.call('read',dict(context_ref=ref,objects=[obj]),'d1-d2.v1')
  except FileNotFoundError:pass
  else:issues.append([key,'scope leak',obj])
 sources=[json.loads(l) for l in (f/'web_search.jsonl').read_text(encoding='utf-8').splitlines() if l];assert len(sources)==len({s['path'] for s in sources})
 for s in sources:
  assert (f/s['path']).exists()
  t=s.get('published_at')
  if t:
   try:
    stamp=datetime.fromisoformat(t.replace('Z','+00:00'))
    if stamp.tzinfo and stamp>datetime.fromisoformat(raw['as_of']):issues.append([key,'future source',s['path']])
   except ValueError:pass
 write_json(out/'visible_inputs'/f'{key}.json',visible)
 rows.append(dict(case_key=key,model=profile,source_count=len(sources),policies=len(visible.get('policies',[])),event_count=len(visible.get('w1',{}).get('events',[])),input_coverage=coverage(raw,ctx,components,evidence,market)))
gold=hashlib.sha256((root/'gold.jsonl').read_bytes()).hexdigest();assert gold=='e9ae0be2529c9f095a7076c50c2a7cebb5d580f6bb25406961bf942abe0717b7'
result=dict(cases=len(rows),gold_unchanged=True,gold_sha256=gold,issues=issues,model_routes=dict(collections.Counter(r['model']['model'] for r in rows)),with_policy=sum(r['policies']>0 for r in rows),with_events=sum(r['event_count']>0 for r in rows),source_total=sum(r['source_count'] for r in rows),rows=rows)
write_json(out/'input_acceptance.json',result);print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2));assert not issues
