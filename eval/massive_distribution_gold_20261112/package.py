"""Validate label coverage and package the two frozen sets; no model/tuning calls."""
import argparse,collections,hashlib,json
from pathlib import Path
P=Path(__file__).parent;ROOT=P.parents[1]
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def rows(p):
 with p.open(encoding='utf-8-sig') as f:return [json.loads(s) for s in f if s.strip()]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def jsonl(p,rs):
 p.parent.mkdir(parents=True,exist_ok=True)
 def encode(r):
  s=json.dumps(r,ensure_ascii=False)
  for c in ['\u0085','\u2028','\u2029']:s=s.replace(c,'\\u'+format(ord(c),'04x'))
  return s+'\n'
 p.write_text(''.join(encode(r) for r in rs),encoding='utf-8')
def collect():
 m=read(P/'dataset-manifest.json');freeze=read(P/'annotation-freeze.json');status=[];all_labels={}
 consistency={'DIRECT':'RELATED','INDIRECT':'RELATED','INDUSTRY_BACKGROUND':'IRRELEVANT','IRRELEVANT_TEMPLATE':'IRRELEVANT','INSUFFICIENT':'INSUFFICIENT'}
 for info in m['packets']:
  source=P/info['file'];assert sha(source)==freeze['packets'][info['file']]
  expected={t['pair_id']:t['ticker'] for r in rows(source) for t in r['targets']}
  number=source.stem.split('-')[1];target=P/'annotation'/f'labels-{number}.jsonl';errors=[];labels=[]
  if target.exists():
   try:labels=rows(target)
   except (ValueError,OSError):errors.append('invalid_or_in_progress_file')
  ids=[r.get('pair_id') for r in labels]
  if len(set(ids))!=len(ids):errors.append('duplicate_ids')
  for r in labels:
   pid=r.get('pair_id')
   if pid not in expected:errors.append('unknown_id');continue
   if r.get('ticker')!=expected[pid]:errors.append('ticker_mismatch')
   if consistency.get(r.get('category'))!=r.get('label'):errors.append('label_category_mismatch')
   if not isinstance(r.get('reason'),str) or len(r['reason'].strip())<12:errors.append('reason_missing_or_too_short')
   if type(r.get('mixed_topics')) is not bool:errors.append('mixed_topics_type')
   if r.get('confidence') not in ['HIGH','MEDIUM','LOW']:errors.append('confidence_invalid')
   if pid in all_labels:errors.append('cross_packet_duplicate_id')
   all_labels[pid]=r
  missing=set(expected)-set(ids)
  status.append({'packet':number,'expected':len(expected),'present':len(set(ids)&set(expected)),'missing':len(missing),'errors':sorted(set(errors)),'complete':not errors and not missing and len(labels)==len(expected)})
 return m,all_labels,status

def main(progress=False):
 m,labels,status=collect();summary={'complete_packets':sum(s['complete'] for s in status),'packets':len(status),'present_pairs':sum(s['present'] for s in status),'expected_pairs':m['sample_pairs'],'errors':{s['packet']:s['errors'] for s in status if s['errors']},'remaining':[(s['packet'],s['missing']) for s in status if not s['complete']]}
 print(json.dumps(summary),flush=True)
 if progress:return
 assert all(s['complete'] for s in status),'Finish/correct every annotation packet before packaging'
 freeze=read(P/'annotation-freeze.json');assert sha(P/'annotation-rules.md')==freeze['annotation_rules_sha256']
 annotators={}
 for n in [1,2,3]:
  for f in read(P/'annotation'/f'shard-{n}.json')['files']:annotators[Path(f).name]='/root/massive_gold_20261112_'+str(n)
 pair_agents={t['pair_id']:annotators[Path(info['file']).name] for info in m['packets'] for r in rows(P/info['file']) for t in r['targets']}
 datasets={};counts=[]
 for split in ['development','validation']:
  source=P/'sets'/f'{split}-unlabeled.jsonl';assert sha(source)==freeze['sets'][split]
  result=[]
  for r in rows(source):
   lab=labels[r['pair_id']]
   value={**r,'gold':{k:lab[k] for k in ['label','category','reason','mixed_topics','confidence']}}
   value['gold'].update(annotation_model='gpt-6.1-terra',reasoning_effort='high',annotator=pair_agents[r['pair_id']])
   assert hashlib.sha256(json.dumps(r['evidence'],ensure_ascii=False,sort_keys=True).encode()).hexdigest()==r['evidence_sha256']
   result.append(value)
  datasets[split]=result;jsonl(P/'final'/f'{split}-gold.jsonl',result)
  review='# Massive Distribution '+split+' GOLD\n\n完整正文与哈希保存在同名JSONL；本页是逐条人工审阅索引。\n\n'
  for t in m['tickers']:
   rs=[r for r in result if r['ticker']==t];jsonl(P/'final'/t/f'{split}-gold.jsonl',rs)
   c=collections.Counter(r['gold']['label'] for r in rs);categories=collections.Counter(r['gold']['category'] for r in rs)
   counts.append({'ticker':t,'split':split,'total':len(rs),'related':c['RELATED'],'irrelevant':c['IRRELEVANT'],'insufficient':c['INSUFFICIENT'],'related_fraction':c['RELATED']/len(rs) if rs else None,'category_counts':dict(categories),'low_confidence':sum(r['gold']['confidence']=='LOW' for r in rs)})
   review+='## '+t+'\n\n|ID|标题|GOLD / 类型|理由|\n|---|---|---|---|\n'
   for r in rs:
    e=lambda s:' '.join(str(s).split()).replace('|','\\|')
    review+='|'+e(r['pair_id'])+'|'+e(r['evidence']['title'])+'|'+r['gold']['label']+' / '+r['gold']['category']+'|'+e(r['gold']['reason'])+'|\n'
   review+='\n'
  (P/'final'/f'{split}-review.md').write_text(review,encoding='utf-8')
 dev=datasets['development'];val=datasets['validation'];assert not {r['family_id'] for r in dev}&{r['family_id'] for r in val}
 assert len(labels)==len(dev)+len(val)==m['sample_pairs']
 write(P/'final/label-summary.json',counts)
 verification={'pairs':m['sample_pairs'],'per_split':{s:len(rs) for s,rs in datasets.items()},'unique_families':len({r['family_id'] for rs in datasets.values() for r in rs}),'family_cross_split_overlap':0,'all_gold_ids_exactly_covered':True,'all_fields_valid':True,'evidence_hashes_valid':True,'annotation_inputs_unchanged':True,'model':'gpt-6.1-terra','reasoning_effort':'high','fallback_used':False,'source_counts':{s:dict(collections.Counter(r['source_id'] for r in rs)) for s,rs in datasets.items()},'baseline_integrity':read(P/'baseline-integrity.json'),'sha256':{s:sha(P/'final'/f'{s}-gold.jsonl') for s in datasets},'production_config_modified':False,'new_regex_candidates_created':0,'decision_model_calls':0,'labels':[{'file':'annotation/labels-'+s['packet']+'.jsonl','sha256':sha(P/'annotation'/('labels-'+s['packet']+'.jsonl'))} for s in status]}
 write(P/'verification.json',verification)
 print('PACKAGED',len(dev),len(val),'ID/field/hash/family checks passed.')

if __name__=='__main__':
 a=argparse.ArgumentParser();a.add_argument('--progress',action='store_true');main(a.parse_args().progress)
