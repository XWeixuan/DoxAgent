"""All-pairs, ticker-local offline dedup experiments; no production mutations.

Run: uv run --no-project --with rapidfuzz --with numpy python <this file>
Content stays in ignored .tmp-codex; outputs contain only candidate evidence snippets.
"""
import collections
import hashlib
import html
import json
import re
import time
import unicodedata
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import numpy as np
from rapidfuzz import fuzz, process

ROOT = Path(__file__).resolve().parents[2]
OUT = Path(__file__).resolve().parent
INPUT = ROOT / '.tmp-codex/message_efficiency_followup_20261117/published_content.jsonl'
lines = [json.loads(s) for s in INPUT.read_text(encoding='utf-8').splitlines()]
manifest = lines.pop(0)
rows = sorted(lines, key=lambda r:(r['ticker'],r['stream_published_at'],r['id']))
assert len(rows)==2065 and len({r['id'] for r in rows})==2065

def norm(text):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',html.unescape(text or ''))).strip().casefold()

def title(r):
    t=r['title']
    if r['source_id']=='google_news_search_rss' and ' - ' in t:
        t=t.rsplit(' - ',1)[0]
    return norm(t)

def url_keys(r):
    p=urlsplit(r['url']);host=(p.hostname or '').lower().removeprefix('www.')
    if host in {'news.google.com','finnhub.io'} or p.path.rstrip('/') in ('','/en/trading/providers.php','/newsviewer'):
        return host,None,None
    query=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if not k.lower().startswith('utm_') and k.lower() not in {'fbclid','gclid','mod','siteid','.tsrc','oc','output','amp'}]
    path=unquote(p.path).rstrip('/');path=re.sub(r'(?:/amp|\.amp)$','',path,flags=re.I)
    strict=urlunsplit(('https',host,path,urlencode(sorted(query)),''))
    # An explicitly broader CANDIDATE key: same publisher, same textual slug.
    # Preserve language through a separate partition; do not use it as proven identity.
    slug=path.split('/')[-1]
    if not slug or re.fullmatch(r'[a-z]{2}(?:-[a-z]+)?',slug,re.I):return host,strict,None
    slug=re.sub(r'\.(?:html?|aspx)$','',slug,flags=re.I)
    slug=re.sub(r'-(?:\d{6,})$','',slug)
    slug=re.sub(r'^\d{8,}[-_]','',slug)
    # Drop publisher suffixes and locale paths only; slug's semantic words stay intact.
    slug=norm(re.sub(r'[-_]+',' ',slug))
    return host,strict,slug if len(slug)>=25 else None

titles=[title(r) for r in rows]
bodies=[norm(r['body']) for r in rows]
urls=[url_keys(r) for r in rows]
full=[r['outcome'] in ('FULL','SHORT_FULL') or r['body_kind']=='FULL' for r in rows]
edges={}
stats={'messages':len(rows),'exported_at':manifest['exported_at'],'input_sha256':hashlib.sha256(INPUT.read_bytes()).hexdigest(), 'body_min_chars':400,'body_similarity':'RapidFuzz fuzz.ratio: normalized Indel/LCS, 2*LCS/(len(a)+len(b))','body_eligible':sum(len(b)>=400 for b in bodies),'full_body_eligible':sum(len(b)>=400 and f for b,f in zip(bodies,full)),'schemes':{}}

def add(i,j,scheme,score=None):
    if i>j:i,j=j,i
    e=edges.setdefault((i,j),{'i':i,'j':j,'schemes':[]})
    if scheme not in e['schemes']:e['schemes'].append(scheme)
    if score is not None:e[scheme+'_score']=float(score)

start=time.monotonic()
for ticker in sorted({r['ticker'] for r in rows}):
    ids=[i for i,r in enumerate(rows) if r['ticker']==ticker]
    for field,keyindex in [('url_normalized',1),('url_slug_exact',2)]:
        groups=collections.defaultdict(list)
        for i in ids:
            host,strict,slug=urls[i];key=(host,(strict if keyindex==1 else slug),rows[i]['language'])
            if key[1]:groups[key].append(i)
        for group in groups.values():
            for pos,i in enumerate(group):
                for j in group[pos+1:]:add(i,j,field)
    mat=process.cdist([titles[i] for i in ids],[titles[i] for i in ids],scorer=fuzz.ratio,score_cutoff=90,dtype=np.float32,workers=2)
    for a,b in zip(*np.where(np.triu(mat,k=1)>=90)):
        i,j=ids[a],ids[b]
        if min(len(titles[i]),len(titles[j]))<25:continue
        sc=mat[a,b]
        add(i,j,'title90',sc)
        if sc>=95:add(i,j,'title95',sc)
        if titles[i]==titles[j]:add(i,j,'title_exact',100)
    bids=[i for i in ids if len(bodies[i])>=400]
    mat=process.cdist([bodies[i] for i in bids],[bodies[i] for i in bids],scorer=fuzz.ratio,score_cutoff=90,dtype=np.float32,workers=2)
    for a,b in zip(*np.where(np.triu(mat,k=1)>=90)):
        i,j=bids[a],bids[b];sc=mat[a,b]
        add(i,j,'body90',sc)
        if sc>=95:
            add(i,j,'body95',sc)
            if full[i] and full[j]:add(i,j,'body95_full',sc)
        if sc>=98:add(i,j,'body98',sc)
    # URL fuzziness is tested within the same publisher and language.
    for pos,i in enumerate(ids):
        host,_,slug=urls[i]
        if not slug:continue
        for j in ids[pos+1:]:
            h2,_,s2=urls[j]
            if host==h2 and s2 and rows[i]['language']==rows[j]['language']:
                score=fuzz.ratio(slug,s2,score_cutoff=95)
                if score>=95:add(i,j,'url_slug95',score)
    print('finished',ticker,round(time.monotonic()-start,1),'seconds',flush=True)

schemes=['url_normalized','url_slug_exact','url_slug95','title_exact','title95','title90','body98','body95','body95_full','body90']
selected={name:[(i,j) for (i,j),e in edges.items() if name in e['schemes']] for name in schemes}
selected['url_or_title95_or_body95']=sorted(set(selected['url_slug95']+selected['url_slug_exact']+selected['title95']+selected['body95']))
selected['title95_and_body95']=sorted(set(selected['title95'])&set(selected['body95_full']))

def groups_for(pairs):
    parent={}
    def find(i):
        parent.setdefault(i,i)
        if parent[i]!=i:parent[i]=find(parent[i])
        return parent[i]
    for i,j in pairs:parent[find(j)]=find(i)
    groups=collections.defaultdict(list)
    for i in parent:groups[find(i)].append(i)
    return [sorted(g) for g in groups.values()]

for name,pairs in selected.items():
    gs=groups_for(pairs)
    # Sequential policy: merge only against an actual retained representative.
    # Avoid A~B~C connected-component chains being silently counted as safe removals.
    adjacency=collections.defaultdict(set)
    for i,j in pairs:adjacency[i].add(j);adjacency[j].add(i)
    retained=[];removed=[]
    for i in range(len(rows)):
        matches=[j for j in retained if j in adjacency[i]]
        if matches:removed.append({'keep':matches[0],'drop':i})
        else:retained.append(i)
    stats['schemes'][name]={'pairs':len(pairs),'components':len(gs),'component_extras':sum(len(g)-1 for g in gs),'greedy_removed':len(removed),'decisions':removed,'groups':gs}

def public_row(i):
    r=rows[i]
    return {k:r.get(k) for k in ('id','ticker','source_id','title','url','language','published_at','stream_published_at','outcome','body_kind')}|{'index':i,'body_chars':len(bodies[i])}

candidate_output=[]
for (i,j),e in sorted(edges.items()):
    e={**e,'a':public_row(i),'b':public_row(j),'title_score':fuzz.ratio(titles[i],titles[j]),'body_score':fuzz.ratio(bodies[i],bodies[j]) if bodies[i] and bodies[j] else None,'both_full':full[i] and full[j]}
    # Compact semantic-risk hints: never use these alone as a duplicate verdict.
    na=collections.Counter(re.findall(r'(?<!\w)[+-]?\d[\d,.]*(?:%|[a-z]{1,2})?',bodies[i]))
    nb=collections.Counter(re.findall(r'(?<!\w)[+-]?\d[\d,.]*(?:%|[a-z]{1,2})?',bodies[j]))
    e['numbers_only_a']=list((na-nb).elements())[:25];e['numbers_only_b']=list((nb-na).elements())[:25]
    candidate_output.append(e)
(OUT/'candidates.json').write_text(json.dumps(candidate_output,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'experiment.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/'message_index.json').write_text(json.dumps([public_row(i) for i in range(len(rows))],ensure_ascii=False),encoding='utf-8')
print(json.dumps({k:({n:{kk:vv for kk,vv in v.items() if kk not in ('decisions','groups')} for n,v in value.items()} if k=='schemes' else value) for k,value in stats.items()},ensure_ascii=False,indent=2))
