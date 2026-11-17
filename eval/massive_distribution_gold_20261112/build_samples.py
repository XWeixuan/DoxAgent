"""Operator-reviewed title enrichment; immutable family split before GOLD annotation."""
import collections,hashlib,inspect,json,random,re,unicodedata
from pathlib import Path
from urllib.parse import parse_qsl,urlencode,urlsplit,urlunsplit

P=Path(__file__).parent;SEED=2026111204
TICKERS=['INTC','MU','BE','COHR','NBIS','CEG','RKLB','SNOW','ONDS','IONQ']
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def rows(p):
 with p.open(encoding='utf-8-sig') as f:return [json.loads(s) for s in f if s.strip()]
def write(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def jsonl(p,v):
 p.parent.mkdir(parents=True,exist_ok=True)
 def line(x):
  s=json.dumps(x,ensure_ascii=False)
  for c in ['\u0085','\u2028','\u2029']:s=s.replace(c,'\\u'+format(ord(c),'04x'))
  return s+'\n'
 p.write_text(''.join(line(x) for x in v),encoding='utf-8')
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def digest(v):return hashlib.sha256(v.encode()).hexdigest()
def norm(v):return re.sub(r'[^\w]','',unicodedata.normalize('NFKC',v).casefold())
def title(v):return re.sub(r'^(?:retransmission|correction|correcting and replacing|updated|update)\s*[:–—-]\s*','',v.strip(),flags=re.I)
def url(v):
 u=urlsplit(v);q=[(k,x) for k,x in parse_qsl(u.query) if not k.lower().startswith('utm_') and k not in ['fbclid','gclid']]
 return urlunsplit((u.scheme.lower(),u.netloc.lower().removeprefix('www.'),u.path.rstrip('/'),urlencode(q),''))

# These expressions choose sampling strata; they are NOT proposed monitoring rules
# and never determine GOLD. Derived from a manual skim of the pooled title catalog.
LIKELY={
 'INTC':r'\b(?:Intel|INTC|Xeon|AMD|EPYC|Ryzen|CPU|CPUs|processor|processors|foundr\w*|wafer|lithograph\w*|ASML|Lam Research|semiconductor design|advanced packaging|AI compute|AI factory|data center development)\b',
 'MU':r'\b(?:Micron|Crucial|DRAM|HBM\w*|NAND|SSDs?|memory|Kioxia|Hynix|Sandisk|wafer bonding|advanced packaging|AI servers?|AI compute|GPU\w*|data.?cent\w*|AI infrastructure|HIVE|IREN|CoreWeave|Nebius|WhiteFiber|Hyperscale Data)\b',
 'BE':r'\b(?:Bloom Energy|fuel cells?|FuelCell|electroly[sz]\w*|hydrogen|power generation|electricity|nuclear|grid|energy storage|solar power|AI (?:campus|data center)|data center power)\b',
 'COHR':r'\b(?:Coherent Corp\w*|Lumentum|Fabrinet|MACOM|POET|Hamamatsu|nLIGHT|photonic\w*|lasers?|optical|optics|fiber|fibre|silicon carbide|SiC substrates?|data.?cent\w*|AI compute|AI infrastructure|GPU cloud|HIVE|IREN|CoreWeave|Nebius|WhiteFiber|Hyperscale Data)\b',
 'NBIS':r'\b(?:Nebius|NBIS|GPU\w*|IaaS|AI infrastructure|AI compute|AI cloud|inference|data.?cent\w*|cloud (?:computing|infrastructure|provider|platform)|Cerebras|CoreWeave|IREN|HIVE)\b',
 'CEG':r'\b(?:Constellation|Calpine|nuclear|reactor\w*|electricity|power generation|power plant|power grid|solar (?:power|energy)|renewable\w*|wind (?:power|energy|farm)|energy storage|fusion energy|utilities|Vistra|Exelon|Duke Energy|NextEra)\b',
 'RKLB':r'\b(?:Rocket Lab|RKLB|spacecraft|spaceflight|satellites?|satellite|launch vehicle|rocket|orbit\w*|SpaceX|NASA|space logistics|Gilat|SpaceMobile|Globalstar|ESCAPADE|Impulse Space)\b',
 'SNOW':r'\b(?:Snowflake|Databricks|ClickHouse|Teradata|BigQuery|database\w*|data (?:integration|pipeline|platform|warehouse|governance|management|lake)|data fabric|Denodo|Matillion|SnapLogic|mParticle|Redis|MongoDB|Informatica|Confluent|Qlik|Boomi|Fivetran|Oracle|SAP)\b',
 'ONDS':r'\b(?:Ondas|ONDS|drones?|UAVs?|UAS|counter.drone|counter.UAS|unmanned aerial|uncrewed aerial|loitering munition|industrial wireless|private wireless|rail wireless|Anterix|Draganfly|Volatus|ParaZero|DroneShield|Skydio|DJI|Shield AI|AeroVironment|military robots?)\b',
 'IONQ':r'\b(?:IonQ|quantum|qubits?|PQC|QKD|Quantinuum|Rigetti|D.Wave|Pasqal|SEALSQ|Xanadu|Oxford Ionics|atomic clock)\b',
}

def main():
 inv=read(P/'inventory.json');archive=rows(P/'complete-body-archive.jsonl');bykey={r['article_key']:r for r in archive}
 pool=[r for r in inv['pool'] if r['current_terms_revision_match'] and r['current_body_decision_match'] and r['article_key'] in bykey]
 # Union exact URL/title/body identities globally, never separately per ticker.
 parent={k:k for k in bykey}
 def find(k):
  while parent[k]!=k:parent[k]=parent[parent[k]];k=parent[k]
  return k
 def union(a,b):
  a,b=find(a),find(b)
  if a!=b:parent[max(a,b)]=min(a,b)
 tags={};translation_tags={}
 for key,a in bykey.items():
  ts=['url:'+url(a['canonical_url']),'title:'+norm(title(a['evidence']['title']))]
  if a.get('body_fingerprint'):ts.append('fingerprint:'+a['body_fingerprint'])
  ts.append('body:'+digest(' '.join(a['evidence']['body'].split())))
  headline=title(a['evidence']['title'])
  # High-confidence translated versions observed in the title skim. Keep the
  # whole release together; do not try to infer generic event similarity.
  for product in ['GCNanoOptimaV','Matrice 5T','Redis Iris','Quantinuum.*Nexus']:
   if re.search(product,headline,re.I):ts.append('translated-product:'+product)
  financial=re.search(r'results|résultats|resultados|Ergebnisse|risultati',headline,re.I)
  reporting=re.search(r'to report|to host|earnings call|announc\w*.*date|remind|notifi|notice|alert',headline,re.I)
  q=re.search(r'\bQ([1-4])\b',headline,re.I)
  if not q:
   qnumber=next((n for n,p in [(1,r'first.quarter|premier.trimestre'),(2,r'second.quarter|deuxième.trimestre'),(3,r'third.quarter|troisième.trimestre|tercer.trimestre|dritte\w*.quartal'),(4,r'fourth.quarter|quatrième.trimestre')] if re.search(p,headline,re.I)),None)
  else:qnumber=int(q.group(1))
  year=re.search(r'20\d\d',headline);issuer=re.findall(r'\w+',headline.casefold())
  if financial and not reporting and qnumber and year and issuer and len(issuer[0])>=4 and issuer[0] not in ['global','company','press','investor']:
   ts.append('translated-earnings:'+issuer[0]+':'+str(qnumber)+':'+year.group())
  # Provider release identity (language variants of one release stay together).
  u=a['canonical_url']
  for pattern,prefix in [(r'globenewswire\.com/news-release/\d{4}/\d{2}/\d{2}/(\d+)/','gnw:'),(r'businesswire\.com/news/home/(\d+)/','bw:')]:
   m=re.search(pattern,u,re.I)
   if m:ts.append(prefix+m.group(1))
  for tag in ts:
   if tag in tags:union(key,tags[tag])
   else:tags[tag]=key
 # Text-shingle Jaccard detects syndicated copies with changed headers/footers.
 # Eight-word shingles avoid merging merely shared issuer boilerplate.
 gramsets={};buckets=collections.defaultdict(list)
 for key,a in bykey.items():
  words=re.findall(r'\w+',unicodedata.normalize('NFKC',a['evidence']['body']).casefold())
  grams={digest(' '.join(words[i:i+8]))[:12] for i in range(max(0,len(words)-7))}
  gramsets[key]=grams
  for g in sorted(grams)[:5]:buckets[g].append(key)
 checked=set();near=[]
 for keys in buckets.values():
  if len(keys)>80:continue
  for i,a in enumerate(keys):
   for b in keys[i+1:]:
    pair=tuple(sorted([a,b]))
    if pair in checked:continue
    checked.add(pair)
    if find(a)==find(b):continue
    x,y=gramsets[a],gramsets[b]
    if min(len(x),len(y))<50:continue
    score=len(x&y)/len(x|y)
    if score>=.82:
     union(a,b);near.append({'article_keys':[a,b],'jaccard':score})
 families=collections.defaultdict(list)
 for key in bykey:families[find(key)].append(key)
 key_family={key:'F'+digest(root)[:14] for root,keys in families.items() for key in keys}
 byfamily=collections.defaultdict(list)
 for r in pool:byfamily[(r['ticker'],key_family[r['article_key']])].append(r)
 pairs=[]
 for (t,fid),members in sorted(byfamily.items()):
  # English preferred only among equivalent copies of the same release.
  # Other language-only releases remain eligible and are not translated.
  best=max(members,key=lambda r:(bykey[r['article_key']]['language']=='en',len(bykey[r['article_key']]['evidence']['body']),r['article_key']))
  a=bykey[best['article_key']];h=bool(re.search(LIKELY[t],a['evidence']['title'],re.I))
  pairs.append({'pair_id':fid+'-'+t,'ticker':t,'family_id':fid,'evidence':a['evidence'],'evidence_sha256':a['evidence_sha256'],'canonical_url':a['canonical_url'],'sources':sorted({r['source_id'] for r in members}),'source_id':a['source_id'],'language':a['language'],'published_at':a['published_at'],'publication_time_basis':a['publication_time_basis'],'first_seen_at':min(r['first_seen_at'] for r in members),'article_ids':sorted(r['article_key'] for r in members),'chosen_article_id':a['article_key'],'body_outcome':a['body_outcome'],'content_revision':a['content_revision'],'terms_revision':inv['revisions'][t],'title_stratum':'business_title' if h else 'other_title','evidence_basis':'retained_verified_complete_result','sampling_hint_only':True})
 by_t=collections.defaultdict(list)
 for r in pairs:by_t[r['ticker']].append(r)
 assignment={}
 # First balance the genuinely scarce pools; globally reuse each family side.
 for t in sorted(TICKERS,key=lambda t:len(by_t[t])):
  counts=collections.Counter(assignment[r['family_id']] for r in by_t[t] if r['family_id'] in assignment)
  for r in sorted(by_t[t],key=lambda r:digest(str(SEED)+r['family_id'])):
   f=r['family_id']
   if f not in assignment:
    s=min(['development','validation'],key=lambda s:(counts[s],digest(f+s)))
    assignment[f]=s;counts[s]+=1
 selected=[];quotas={}
 for t in TICKERS:
  quotas[t]={}
  for split in ['development','validation']:
   p=[r for r in by_t[t] if assignment[r['family_id']]==split];n=min(50,len(p))
   bins={k:[r for r in p if r['title_stratum']==k] for k in ['business_title','other_title']}
   rng=random.Random(int(digest(str(SEED)+t+split)[:12],16))
   target=min(len(bins['business_title']),round(n*.4));other=min(len(bins['other_title']),n-target)
   target=min(len(bins['business_title']),n-other)
   chosen=rng.sample(bins['business_title'],target)+rng.sample(bins['other_title'],other)
   chosen=[dict(r,split=split) for r in chosen];selected.extend(chosen)
   quotas[t][split]={'eligible':len(p),'sampled':n,'title_strata_pool':{k:len(v) for k,v in bins.items()},'title_strata_sample':dict(collections.Counter(r['title_stratum'] for r in chosen))}
 selected.sort(key=lambda r:(r['split'],r['ticker'],r['pair_id']))
 assert len({r['pair_id'] for r in selected})==len(selected)
 assert not {r['family_id'] for r in selected if r['split']=='development'}&{r['family_id'] for r in selected if r['split']=='validation'}
 # Freeze annotation packets by global family; share one full article among targets.
 packets=collections.defaultdict(list)
 for r in selected:packets[(r['family_id'],r['evidence_sha256'],r['split'])].append(r)
 batch=[];batches=[];chars=0
 for (fid,eh,split),rs in sorted(packets.items(),key=lambda kv:(kv[0][2],kv[0][0])):
  e=rs[0]['evidence'];packet={'family_id':fid,'split':split,'evidence_sha256':eh,'evidence':e,'targets':[{'pair_id':r['pair_id'],'ticker':r['ticker']} for r in rs]}
  length=len(json.dumps(packet,ensure_ascii=False))
  if batch and (chars+length>260000 or sum(len(x['targets']) for x in batch)+len(rs)>85):batches.append(batch);batch=[];chars=0
  batch.append(packet);chars+=length
 if batch:batches.append(batch)
 for i,rs in enumerate(batches,1):jsonl(P/'annotation'/f'packet-{i:02}.jsonl',rs)
 for split in ['development','validation']:
  rs=[r for r in selected if r['split']==split];jsonl(P/'sets'/f'{split}-unlabeled.jsonl',rs)
  for t in TICKERS:jsonl(P/'sets'/t/f'{split}-unlabeled.jsonl',[r for r in rs if r['ticker']==t])
 jsonl(P/'deduplicated-positive-pool.jsonl',pairs)
 write(P/'family-map.json',{'article_to_family':key_family,'near_duplicate_merges':near})
 manifest={'seed':SEED,'per_ticker_per_split_target':50,'sources':list(inv['sources']),'tickers':TICKERS,'snapshot_at':inv['snapshot_at'],'body_snapshot_at':read(P/'body-export-receipt.json')['snapshot_at'],'raw_positive_pairs':len(inv['pool']),'stale_revision_pairs_excluded':sum(not r['current_terms_revision_match'] for r in inv['pool']),'eligible_current_positive_article_ticker_rows':len(pool),'frozen_complete_articles':len(archive),'global_dedup_families':len(families),'dedup_positive_pairs':len(pairs),'per_ticker_dedup_pool':{t:len(by_t[t]) for t in TICKERS},'quotas':quotas,'sample_pairs':len(selected),'sample_unique_families':len({r['family_id'] for r in selected}),'packet_count':len(batches),'packets':[{'file':f'annotation/packet-{i:02}.jsonl','pairs':sum(len(x['targets']) for x in rs),'articles':len(rs),'characters':sum(len(json.dumps(x,ensure_ascii=False)) for x in rs)} for i,rs in enumerate(batches,1)],'sampling':'Current regex-positive pool; title-screened strata target 40% business titles, remainder other titles. No GOLD used. Global family-balanced split, no family crosses splits.','limitations':['Only regex-recalled full-body inputs; cannot estimate full-market recall.','Intentional title enrichment: label percentages and unweighted precision are not natural production rates.','Only current matching decision/body revisions retained; stale-revision positives excluded.','Translations without a common provider identity or lexical similarity may evade deterministic grouping.','Business Wire full-body availability is sparse; all five sources are pooled, no invented per-source quota.'],'inputs':{n:sha(P/n) for n in ['inventory.json','complete-body-archive.jsonl']},'outputs':{s:sha(P/'sets'/f'{s}-unlabeled.jsonl') for s in ['development','validation']}}
 write(P/'dataset-manifest.json',manifest)
 print(json.dumps({k:manifest[k] for k in ['sample_pairs','sample_unique_families','per_ticker_dedup_pool','packet_count']},ensure_ascii=False))
 print('QUOTAS',json.dumps({t:{s:x['sampled'] for s,x in q.items()} for t,q in quotas.items()}))

if __name__=='__main__':main()
