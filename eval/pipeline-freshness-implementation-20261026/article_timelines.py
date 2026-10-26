"""Unique newly observed article keys, retaining failed and unpublished outcomes."""
import json
import gzip
import sys
from pathlib import Path
from doxagent.message_bus_v2.cadence import instant,distribution

p=Path(sys.argv[1]);d=json.loads(p.read_text(encoding='utf-8-sig')) if p.exists() else json.loads(gzip.decompress(p.with_name(p.stem+'.metadata.json.gz').read_bytes()))
articles=[]
groups={}
lineage_path=p.parent/'article-logical-lineage.json'
lineage=json.loads(lineage_path.read_text(encoding='utf-8-sig')) if lineage_path.exists() else {}
group_ids={}
for b in sorted(d['new_body_records'],key=lambda b:instant(b['first_observed_at'])):
    identities=set(map(tuple,lineage.get(b['fetch_key'],[])))
    key=next((k for k,v in group_ids.items() if identities & v),None) or b.get('final_url') or b['url']
    group_ids.setdefault(key,set()).update(identities)
    if key not in groups:groups[key]={**b,'fetch_keys':[]}
    groups[key]['fetch_keys'].append(b['fetch_key'])
    if b.get('outcome') in {'FULL','SHORT_FULL'}:groups[key]['outcome']=b['outcome']
for body in groups.values():
    jobs=[j for j in d['jobs'] if j['body_result_key'] in body['fetch_keys'] and j['work_kind']=='new_article'
          and not j['bootstrap'] and not j['known_article_raw_id']]
    if not jobs or body['legacy_result'] or body['prior_publication_raw_id']:continue
    traces=[j['pipeline_trace'] for j in jobs if j.get('pipeline_trace')]
    captures={t['capture_id'] for t in traces}
    publications=[r['payload'] for r in d['delivery_receipts'] if r.get('payload',{}).get('pipeline_trace',{}).get('capture_id') in captures]
    earliest=min([body['first_observed_at']]+[t['listing_received_at'] for t in traces],key=instant)
    row={**body,'listing_received_at':earliest,'candidate_prepared_at':min((t['candidate_prepared_at'] for t in traces),default=None),
         'job_created_at':min((j['created_at'] for j in jobs),key=instant),'fetch_started_at':min((j['budget_started_at'] for j in jobs if j['budget_started_at']),key=instant,default=None),
         'job_statuses':[j['status'] for j in jobs],'readable_at':min((r['readable_at'] for r in publications),key=instant,default=None),
         'publication_rows':len(publications),'censored':not publications}
    executions=[r for r in d.get('body_executions',[]) if r.get('body_result_key') in body['fetch_keys'] and r.get('outcome') in {'FULL','SHORT_FULL'}]
    full_times=[r['completed_at'] for r in executions]+[j['result']['completed_at'] for j in jobs if (j.get('result') or {}).get('outcome') in {'FULL','SHORT_FULL'} and (j.get('result') or {}).get('completed_at')]
    row['first_full_at']=min(full_times,key=instant,default=None)
    full_publications=[r for r in publications if any(o in {'FULL','SHORT_FULL'} for stream in r.get('streams',[]) for o in stream.get('body_outcomes',[]))]
    row['full_readable_at']=min((r['readable_at'] for r in full_publications),key=instant,default=None)
    row['initial_outcome']=(jobs[0].get('result') or {}).get('outcome')
    row['stages_seconds']={key:(instant(value)-instant(earliest)).total_seconds() if value else None for key,value in
        [('candidate_prepared',row['candidate_prepared_at']),('job_created',row['job_created_at']),('fetch_started',row['fetch_started_at']),('body_full',row['first_full_at']),('consumable',row['readable_at'])]}
    articles.append(row)
full=sum(a['outcome'] in {'FULL','SHORT_FULL'} for a in articles)
timings=[a['stages_seconds']['consumable'] for a in articles if a['readable_at']]
out={'since':d['since'],'at':d['at'],'new_articles':len(articles),'full_articles':full,'full_ratio':full/len(articles) if articles else None,
    'initial_full_articles':sum(a['initial_outcome'] in {'FULL','SHORT_FULL'} for a in articles),
    'full_consumable_articles':sum(bool(a['full_readable_at']) for a in articles),'consumable_articles':len(timings),'within_10_seconds':sum(t<=10 for t in timings),'uncompleted_or_unpublished':sum(a['censored'] for a in articles),
    'latency_seconds':distribution(timings),'articles':articles}
p.with_name(p.stem+'-articles.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:v for k,v in out.items() if k!='articles'}))
for a in articles:print(json.dumps({k:a[k] for k in ('source_id','outcome','job_statuses','stages_seconds')}))
