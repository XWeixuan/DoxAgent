"""Human-reviewed edge ledger and conservative duplicate lower bound.

The verdicts below reflect the inspected retained bodies and token differences.
They are evaluation labels, not an automatic production classifier.
"""
import collections
import json
import re
from pathlib import Path

OUT=Path(__file__).resolve().parent
exp=json.loads((OUT/'experiment.json').read_text(encoding='utf-8'))
edges={(e['i'],e['j']):e for e in json.loads((OUT/'candidates.json').read_text(encoding='utf-8'))}
idx=json.loads((OUT/'message_index.json').read_text(encoding='utf-8'))

fact_changes={
    (1116,1161):'Same D-Wave notice changes lead-plaintiff deadline Dec 15 to Dec 14, and exchange label; retain discrepancy.',
    (1315,1369):'Micron update changes premarket 4.4%/$743.08 to early trading 7.1%/$723.09.',
    (1670,1672):'Nebius article changes market snapshot 8.8%/$105.70 versus 7.9%/$106.70.',
    (1672,1677):'Nebius IBKR versions change market snapshot 7.9%/$106.70 to 8.8%/$105.70.',
}
enrichment={
    (55,82):'Same ZutaCore release, but BW adds model conditions, temperature, liquid/air split and baseline pPUE; merge richer evidence, not blind deletion.',
    (353,382):'Same ZutaCore release; BW adds performance qualification footnote.',
    (657,708):'Same ZutaCore release; BW adds performance qualification footnote.',
    (1334,1367):'Same ZutaCore release; BW adds performance qualification footnote.',
    (1640,1681):'Same ZutaCore release; BW adds performance qualification footnote.',
    (508,634):'Robbins notice shares main text; FinancialContent also retains an Oct 29/date/50-year-decline caption absent in the other version. Conservatively preserve richer evidence.',
    (782,906):'Same title and loose URL slug, but later retained body adds the full alleged Intel misstatements. Do not drop the content upgrade.',
}
manual_safe={
    (603,609):'Both complete 208-character Intel correction bodies exactly identical; short-body full-match exception.',
    (616,619):'Same DJS release; explicit BusinessWire source locator and contact/footer differences only.',
    (615,627):'Same Investing premarket article; added promotions and related-story links, main facts unchanged.',
    (709,729):'Same Investing chip-selloff article; added promotions and related-story links, main facts unchanged.',
    (1366,1376):'Same Investing chip-selloff article for MU; added promotions and related-story links.',
    (1038,1040):'Classiq/Ventum release reprinted with an extractive opening summary; main release unchanged, bios/footer differ.',
    (1908,1925):'Same Stocktwits space-stock story; Asianet adds share controls, unrelated sports results and site furniture.',
    (2052,2054):'Same Citi enterprise-software article; promotion and related-story tails differ, main facts unchanged.',
}

safe=set()
ledger=[]
for pair,e in edges.items():
    if 'body95' not in e['schemes'] and pair not in manual_safe and pair not in fact_changes and pair not in enrichment:continue
    if pair in fact_changes:verdict='FACT_CHANGED';note=fact_changes[pair]
    elif pair in enrichment:verdict='ENRICHMENT';note=enrichment[pair]
    else:
        verdict='DUPLICATE_NO_NEW_ARTICLE_FACT'
        note=manual_safe.get(pair,'Retained article facts match; differences are formatting, source/contact/disclaimer, chart/site furniture or redundant summary.')
        safe.add(pair)
    ledger.append({'i':pair[0],'j':pair[1],'a_id':idx[pair[0]]['id'],'b_id':idx[pair[1]]['id'],'ticker':idx[pair[0]]['ticker'],'verdict':verdict,'note':note})

parent={}
def find(i):
    parent.setdefault(i,i)
    if parent[i]!=i:parent[i]=find(parent[i])
    return parent[i]
for i,j in safe:parent[find(j)]=find(i)
def confirmed(i,j):return i in parent and j in parent and find(i)==find(j)

summary={'note':'Confirmed counts are conservative, retained-input human-review results; other candidates are not automatically false or safe.','schemes':{},'safe_edges':len(safe)}
for name,s in exp['schemes'].items():
    counts=collections.Counter();decisions=[]
    for d in s['decisions']:
        i,j=sorted((d['keep'],d['drop']));pair=(i,j)
        if pair in fact_changes:v='FACT_CHANGED'
        elif pair in enrichment:v='ENRICHMENT'
        elif confirmed(i,j):v='DUPLICATE_CONFIRMED'
        elif any(re.search(r' -(?:2|3|4)-$',idx[k]['title']) for k in (i,j)) and ('CFA ' in idx[i]['title'] or 'Morning Report' in idx[i]['title']):v='DISTINCT_SERIES_PART'
        elif idx[i]['title'].startswith(('Form 4 ','Form 144 ')):v='UNVERIFIED_FILING_IDENTITY'
        else:v='UNVERIFIED'
        counts[v]+=1;decisions.append({**d,'verdict':v})
    summary['schemes'][name]={'raw_removed':s['greedy_removed'],'review':dict(counts),'decisions':decisions}

# Re-run chronological representative matching with only reviewed-safe edges.
adj=collections.defaultdict(set)
for i,j in safe:adj[i].add(j);adj[j].add(i)
kept=[];drops=[]
for i in range(len(idx)):
    matches=[j for j in kept if j in adj[i]]
    if matches:drops.append({'keep':matches[0],'drop':i,'ticker':idx[i]['ticker'],'source_id':idx[i]['source_id']})
    else:kept.append(i)
summary['reviewed_safe_policy']={'removed':len(drops),'share':len(drops)/2065,'decisions':drops,'by_source':dict(collections.Counter(d['source_id'] for d in drops)),'by_ticker':dict(collections.Counter(d['ticker'] for d in drops))}
(OUT/'adjudication_ledger.json').write_text(json.dumps(ledger,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'adjudication_summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({k:({name:{kk:vv for kk,vv in value.items() if kk!='decisions'} for name,value in v.items()} if k=='schemes' else {kk:vv for kk,vv in v.items() if kk!='decisions'} if isinstance(v,dict) else v) for k,v in summary.items()},ensure_ascii=False,indent=2))
