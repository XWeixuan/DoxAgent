"""Read-only local sizing prototype, not production implementation."""
import collections
import html
import json
import re
import sys
import time
import unicodedata
from bisect import bisect_left, bisect_right, insort
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote,urlsplit

from rapidfuzz.fuzz import ratio

ROOT=Path(__file__).resolve().parents[2]
OUT=Path(__file__).resolve().parent
inp=ROOT/'.tmp-codex/message_efficiency_followup_20261117/published_content.jsonl'
rows=[json.loads(line) for line in inp.read_text(encoding='utf-8').splitlines()][1:]
rows.sort(key=lambda r:(r['stream_published_at'],r['ticker'],r['id']))
def norm(s):return ' '.join(unicodedata.normalize('NFKC',html.unescape(s or '')).split()).casefold()
def keys(r):
    title=r['title'].rsplit(' - ',1)[0] if r['source_id']=='google_news_search_rss' and ' - ' in r['title'] else r['title']
    p=urlsplit(r['url']);host=(p.hostname or '').lower().removeprefix('www.')
    slug=unquote(p.path).rstrip('/').rsplit('/',1)[-1]
    slug=re.sub(r'\.(?:html?|aspx)$','',slug,flags=re.I);slug=re.sub(r'-\d{6,}$','',slug)
    slug=norm(re.sub(r'[-_]+',' ',slug))
    loose=(host,slug,r['language']) if host not in {'news.google.com','finnhub.io'} and p.path.rstrip('/') not in ('','/en/trading/providers.php','/newsviewer') and len(slug)>=25 else None
    body=norm(r['body']) if r['outcome'] in ('FULL','SHORT_FULL') or r['body_kind']=='FULL' else ''
    if not 1<=len(body)<=65536:body=''
    return loose,norm(title),body

def percentile(xs,q):return sorted(xs)[min(len(xs)-1,int((len(xs)-1)*q))] if xs else 0
def run():
    state={};dur=[];comparisons=[];hits=collections.Counter();decisions=[];expired=evicted=0
    for r in rows:
        if r['source_id']=='ibkr_news':continue
        started=time.perf_counter_ns();url,title,body=keys(r)
        stamp=datetime.fromisoformat(r['stream_published_at'].replace('Z','+00:00')).timestamp()
        bucket=state.setdefault(r['ticker'],{'q':collections.deque(),'byid':{},'url':{},'title':{},'lengths':[]})
        def remove(k):
            old=bucket['byid'].pop(k,None)
            if not old:return
            u,t,b,ts=old
            if u and bucket['url'].get(u)==k:bucket['url'].pop(u)
            if t and bucket['title'].get(t)==k:bucket['title'].pop(t)
            if b:
                j=bisect_left(bucket['lengths'],(len(b),k))
                if j<len(bucket['lengths']) and bucket['lengths'][j]==(len(b),k):bucket['lengths'].pop(j)
        while bucket['q'] and (bucket['q'][0][0]<stamp-43200 or len(bucket['byid'])>=1024):
            ts,k=bucket['q'].popleft();expired+=int(ts<stamp-43200);evicted+=int(ts>=stamp-43200);remove(k)
        reason=peer=None;n=0
        if url and url in bucket['url']:reason,peer='url',bucket['url'][url]
        elif title and title in bucket['title']:reason,peer='title',bucket['title'][title]
        elif body:
            # Exact length bound for normalized Indel similarity >=95%.
            lo=bisect_left(bucket['lengths'],(int(len(body)*.95/1.05),''))
            hi=bisect_right(bucket['lengths'],(int(len(body)*1.05/.95)+1,'\uffff'))
            for _,k in reversed(bucket['lengths'][lo:hi]):
                n+=1
                if ratio(body,bucket['byid'][k][2],score_cutoff=95)>=95:reason,peer='body',k;break
        comparisons.append(n)
        if reason:
            hits[reason]+=1;decisions.append({'drop':r['id'],'keep':peer,'ticker':r['ticker'],'gate':reason})
        else:
            k=r['id'];bucket['byid'][k]=(url,title,body,stamp);bucket['q'].append((stamp,k))
            if url:bucket['url'][url]=k
            if title:bucket['title'][title]=k
            if body:insort(bucket['lengths'],(len(body),k))
        dur.append((time.perf_counter_ns()-started)/1e6)
    unique_bodies={v[2] for st in state.values() for v in st['byid'].values() if v[2]}
    return {'hits':dict(hits),'removed':sum(hits.values()),'decisions':decisions,'latency_ms':{'p50':percentile(dur,.5),'p95':percentile(dur,.95),'p99':percentile(dur,.99),'max':max(dur)},'body_comparisons':{'total':sum(comparisons),'p95':percentile(comparisons,.95),'max':max(comparisons)},'retained_records':sum(len(st['byid']) for st in state.values()),'max_ticker_records':max(len(st['byid']) for st in state.values()),'unique_bodies':len(unique_bodies),'body_string_bytes':sum(sys.getsizeof(b) for b in unique_bodies),'expired':expired,'capacity_evicted':evicted}
out={'scope':'local prototype; excludes IBKR from incoming gates AND reference pool; 12h/1024 ticker cap; full BODY 1..65536 chars; Google RSS title publisher suffix convention; same ticker; rights scopes not reconstructed','runs':[run() for _ in range(3)]}
assert len({r['removed'] for r in out['runs']})==1
(OUT/'gate-prototype.json').write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'scope':out['scope'],'runs':[{k:v for k,v in r.items() if k!='decisions'} for r in out['runs']]},ensure_ascii=False,indent=2))
