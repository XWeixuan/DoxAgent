"""Recompute every report total from saved, read-only evidence."""
import collections
import csv
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REPO = ROOT.parents[1]
sys.path.insert(0, str(REPO / 'src'))
from doxagent.message_bus_v2.deduplication import article_url

def read(name):
    return json.loads((ROOT / name).read_text(encoding='utf-8-sig'))

read_data = read('read_messages.json')
bus = read('bus_evidence.json')
traces = read('relevance_trace.json')['samples']
overview = read('overview.json')
catalog = json.loads((REPO / 'src/doxagent/message_bus_v2/source_catalog.json').read_text(encoding='utf-8'))['sources']
rows = [r for r in read_data['messages'] if r['day'] == '2026-11-16']
prev = [r for r in read_data['messages'] if r['day'] == '2026-11-09']
bus_by_id = {r['id']:r for r in bus['messages']}
assert len({r['id'] for r in rows}) == len(rows) == 2065
assert len(prev) == 1454 and not bus['missing']
counts, before = (collections.Counter(r['source_id'] for r in rs) for rs in (rows, prev))
massive_ids = sorted(s for s in counts if s.endswith('_massive'))
summary = {'count': len(rows), 'previous': len(prev), 'delta':len(rows)-len(prev), 'massive_total':sum(counts[s] for s in massive_ids), 'massive_sources':massive_ids, 'sources':[]}
for sid in sorted(set(counts)|set(before), key=lambda s:(-counts[s],s)):
    rs = [r for r in rows if r['source_id']==sid]
    summary['sources'].append({'source_id':sid, 'applicability':catalog.get(sid,{}).get('applicability','RETIRED'), 'n':counts[sid], 'previous':before[sid], 'delta':counts[sid]-before[sid], 'unique_article_keys':len({bus_by_id[r['id']]['article_key'] for r in rs}), 'unique_titles':len({r['title'] for r in rs}), 'tickers':','.join(sorted({r['ticker'] for r in rs}))})
assert sum(r['n'] for r in summary['sources'])==2065
assert sum(r['delta'] for r in summary['sources'])==611
summary['massive_growth_share'] = summary['massive_total']/summary['delta']
summary['non_massive_total'] = summary['count'] - summary['massive_total']
summary['non_massive_delta'] = summary['non_massive_total'] - summary['previous']
summary['days'] = dict(sorted(collections.Counter(r['day'] for r in read_data['messages']).items()))
summary['tickers'] = {t:{'n':sum(r['ticker']==t for r in rows),'previous':sum(r['ticker']==t for r in prev)} for t in sorted({r['ticker'] for r in rows})}
summary['scope'] = dict(collections.Counter(catalog.get(r['source_id'],{}).get('applicability') for r in rows))
summary['dedup'] = {}
for name, field in [('logical','message_version'),('article','article_key'),('body','body_fingerprint'),('url','url')]:
    groups = collections.defaultdict(list)
    eligible = 0
    for r in rows:
        b = bus_by_id[r['id']]
        if name=='logical': identity=(b.get(field) or {}).get('logical_message_id')
        elif name=='url':identity=article_url(r['url'], (b.get('identity_evidence') or {}).get('url_kind','unknown'))
        else:identity=b.get(field)
        if not identity:continue
        eligible+=1
        groups[(r['ticker'],identity)].append(r)
    duplicates=[g for g in groups.values() if len(g)>1]
    summary['dedup'][name]={'eligible':eligible,'groups':len(duplicates),'extra':sum(len(g)-1 for g in duplicates)}
summary['templates'] = []
for sid in ['quantum_zeitgeist','dcd_infrastructure_rss']:
    for day in ['2026-11-09','2026-11-16']:
        rs=[r for r in traces if r['day']==day and r['source_id']==sid]
        changed=[r for r in rs if r['original_regex'] and not r['without_template_regex']]
        summary['templates'].append({'source_id':sid,'day':day,'messages':len(rs),'contains_template':sum(r['footer_removed_chars']>0 for r in rs),'regex_lost':len(changed),'unique_affected_articles':len({r['url'] for r in changed})})
review_sources=massive_ids+['google_news_search_rss','yahoo_finance_news','huggingnews_rss','quantum_zeitgeist','technews_hardware','dcd_infrastructure_rss','tomshardware_rss','wccftech_hardware','digitimes_semiconductors','trendforce_news','thelec_semiconductors_rss','data_center_frontier','etnews_rss','investing_ticker_news','ibkr_news','finnhub_company_news','reuters_site_search','thefly_ticker_news']
review_rows=[r for r in rows if r['source_id'] in review_sources]
summary['title_review']={'sources':len(set(review_sources)), 'source_ids':sorted(review_sources), 'messages':len(review_rows),'source_title_entries':len({(r['source_id'],r['title']) for r in review_rows}),'share':len(review_rows)/len(rows)}
legal=re.compile(r'class[- ]action|lawsuit|shareholder alert|investor alert|investigation|lead plaintiff|deadline|securities fraud',re.I)
market=re.compile(r'\bmarket\b.*(?:forecast|report|size|worth|trends)|global .*\bmarket\b',re.I)
summary['massive_title_candidates']=[]
for s in massive_ids:
    rs=[r for r in rows if r['source_id']==s]
    summary['massive_title_candidates'].append({'source_id':s,'legal_messages':sum(bool(legal.search(r['title'])) for r in rs),'legal_urls':len({r['url'] for r in rs if legal.search(r['title'])}),'market_messages':sum(bool(market.search(r['title'])) for r in rs),'market_urls':len({r['url'] for r in rs if market.search(r['title'])})})
for day in ['2026-11-09','2026-11-16']:
    groups=collections.Counter((r['ticker'],re.sub(r'\s+',' ',r['title'] or '').casefold().strip()) for r in read_data['messages'] if r['day']==day)
    summary.setdefault('same_title',{})[day]={'groups':sum(v>1 for v in groups.values()),'extra':sum(v-1 for v in groups.values() if v>1)}
summary['industry_passes'] = {}
for sid in ['quantum_zeitgeist','huggingnews_rss','technews_hardware','dcd_infrastructure_rss']:
    summary['industry_passes'][sid]={}
    for day in ['2026-11-09','2026-11-16']:
        rs=[r for r in bus['messages'] if r['day']==day and r['source_id']==sid]
        summary['industry_passes'][sid][day]={'n':len(rs),'article_keys':len({r['article_key'] for r in rs}),'mode':dict(collections.Counter((r.get('admission_context') or {}).get('mode') for r in rs)),'passes':dict(collections.Counter('regex' if (r['distribution'] or {}).get('regex_hit') else 'model' if ((r['distribution'] or {}).get('jev_result') or 0)>=.5 else 'other' for r in rs))}
(ROOT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
with (ROOT/'source_counts.csv').open('w',encoding='utf-8-sig',newline='') as f:
    writer=csv.DictWriter(f,fieldnames=list(summary['sources'][0]));writer.writeheader();writer.writerows(summary['sources'])
with (ROOT/'title_review.jsonl').open('w',encoding='utf-8') as f:
    for r in review_rows:f.write(json.dumps(r,ensure_ascii=False)+'\n')
print(json.dumps({k:v for k,v in summary.items() if k not in ('sources','industry_passes')},ensure_ascii=False,indent=2))
