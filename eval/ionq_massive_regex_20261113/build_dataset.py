"""Fixed article-family split; old nine-ticker exposure is development-only."""
import hashlib,json,random,re,unicodedata
from collections import Counter,defaultdict
from pathlib import Path
from urllib.parse import urlsplit,urlunsplit

P=Path(__file__).parent;SEED=2026111309
def read(p):return json.loads(p.read_text(encoding='utf-8-sig'))
def rows(p):
    with p.open(encoding='utf-8-sig') as f:return [json.loads(line) for line in f if line.strip()]
def h(s):return hashlib.sha256(s.encode()).hexdigest()
def norm(s):return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',s or '').casefold()).strip()
def url(s):
    p=urlsplit(s or '');return urlunsplit((p.scheme,p.netloc.lower(),p.path.rstrip('/'),'',''))
def provider_id(value):
    for pattern,prefix in [(r'globenewswire.com/news-release/\d+/\d+/\d+/(\d+)','gnw:'),(r'prnewswire.com/.+-(\d+)\.html','prn:'),(r'newsfilecorp.com/release/(\d+)','tmx:'),(r'businesswire.com/news/home/(\d+)','bw:')]:
        match=re.search(pattern,value or '')
        if match:return prefix+match.group(1)
    return ''
def save(p,v):p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
def lines(p,v):
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(''.join(json.dumps(x,ensure_ascii=False).replace('\u2028','\\u2028').replace('\u2029','\\u2029')+'\n' for x in v),encoding='utf-8')

def main():
    archive=read(P/'raw-pool.json');a=archive['recalled_articles'];parent=list(range(len(a)))
    def find(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    def union(i,j):parent[find(j)]=find(i)
    seen={};grams=[];titles=[]
    for i,r in enumerate(a):
        titles.append(set(re.findall(r'\w+',norm(r['evidence']['title']))))
        w=re.findall(r'\w+',norm(r['evidence']['body']))
        grams.append({h(' '.join(w[k:k+8]))[:14] for k in range(max(0,len(w)-7))})
        for key in [('provider',provider_id(r['canonical_url'])),('url',url(r['canonical_url'])),('body',h(norm(r['evidence']['body']))),('evidence',r['evidence_sha256']),('title',norm(r['evidence']['title']))]:
            if not key[1]:continue
            if key in seen:union(i,seen[key])
            else:seen[key]=i
    near=[]
    for i in range(len(a)):
        for j in range(i):
            if find(i)==find(j) or min(len(grams[i]),len(grams[j]))<100:continue
            tj=len(titles[i]&titles[j])/max(1,len(titles[i]|titles[j]))
            if tj<.5:continue
            bj=len(grams[i]&grams[j])/max(1,len(grams[i]|grams[j]))
            if bj>=.82:union(i,j);near.append([a[i]['article_key'],a[j]['article_key'],round(bj,4)])
    # Read only identity fields; never import old GOLD or validation labels.
    prior=Path('C:/Users/WEIXUANXIE/Desktop/DoxAgent/eval/massive_distribution_gold_20261112/sets')
    old_urls=set();old_hashes=set();old_keys=set()
    for p in prior.glob('*-unlabeled.jsonl'):
        for r in rows(p):
            if r['ticker']=='IONQ':continue  # IONQ was not tuned or opened in the nine-ticker work.
            old_urls.add(url(r.get('canonical_url')));old_hashes.add(r.get('evidence_sha256'));old_keys.update(r.get('article_ids',[]))
    families=defaultdict(list)
    for i,r in enumerate(a):families[find(i)].append(r)
    pool=[]
    title_hint=re.compile(r'\b(?:IonQ|quantum|qubits?|QPU|Rigetti|D[ -]?Wave|Quantinuum|PQC|QKD|post.quantum|atomic clocks?|Oxford Ionics)\b',re.I)
    for members in families.values():
        r=max(members,key=lambda x:(x['language']=='en',x['delivery_mode']=='production',len(x['evidence']['body'])))
        fid='IONQF'+h('|'.join(sorted(url(x['canonical_url']) for x in members)))[:16]
        exposed=any(url(x['canonical_url']) in old_urls or x['evidence_sha256'] in old_hashes or x['article_key'] in old_keys for x in members)
        pool.append({'pair_id':fid+'-IONQ','ticker':'IONQ','family_id':fid,'evidence':r['evidence'],'evidence_sha256':r['evidence_sha256'],'canonical_url':r['canonical_url'],'source_id':r['source_id'],'sources':sorted({m['source_id'] for m in members}),'language':r['language'],'published_at':r['published_at'],'publication_time_basis':r['publication_time_basis'],'body_outcome':r['body_outcome'],'first_seen_at':r['first_seen_at'],'article_ids':sorted(x['article_key'] for x in members),'chosen_article_id':r['article_key'],'prior_nine_ticker_exposure':exposed,'title_hint':bool(title_hint.search(r['evidence']['title']))})
    save(P/'pool-summary.json',{'raw':len(a),'families':len(pool),'validation_eligible':sum(not x['prior_nine_ticker_exposure'] for x in pool),'title_hint':dict(Counter(x['title_hint'] for x in pool)),'sources':dict(Counter(x['source_id'] for x in pool)),'near_merges':near})
    lines(P/'deduplicated-pool.jsonl',pool)
    if len(pool)<100 or sum(not x['prior_nine_ticker_exposure'] for x in pool)<50:
        print(json.dumps(read(P/'pool-summary.json')));return
    rng=random.Random(SEED)
    def choose(candidates,n):
        yes=sorted([x for x in candidates if x['title_hint']],key=lambda x:x['pair_id']);no=sorted([x for x in candidates if not x['title_hint']],key=lambda x:x['pair_id'])
        rng.shuffle(yes);rng.shuffle(no)
        ny=min(20,len(yes));nn=min(n-ny,len(no));ny=min(n-nn,len(yes))
        result=yes[:ny]+no[:nn];rng.shuffle(result);return result
    validation=choose([x for x in pool if not x['prior_nine_ticker_exposure']],50)
    used={x['family_id'] for x in validation};development=choose([x for x in pool if x['family_id'] not in used],50)
    assert len(validation)==len(development)==50
    selected={'development':development,'validation':validation}
    for split,rs in selected.items():
        for r in rs:r['split']=split
        lines(P/'sets'/f'{split}-unlabeled.jsonl',rs)
        # Labelers never see baseline decisions, source enrichment strata or old GOLD.
        blind=[{k:r[k] for k in ['pair_id','ticker','family_id','evidence','canonical_url','source_id','language']} for r in rs]
        if split=='development':
            for n in range(2):lines(P/'annotation'/f'development-{n+1}.jsonl',blind[n*25:(n+1)*25])
        else:lines(P/'annotation'/'validation.jsonl',blind)
    manifest={'created_at':archive['snapshot_at'],'seed':SEED,'target':[50,50],'baseline_terms_revision':archive['terms_revision'],'pool':read(P/'pool-summary.json'),'counts':{s:len(rs) for s,rs in selected.items()},'family_overlap':len({x['family_id'] for x in development}&{x['family_id'] for x in validation}),'validation_prior_nine_ticker_exposure':sum(x['prior_nine_ticker_exposure'] for x in validation),'development_prior_exposure':sum(x['prior_nine_ticker_exposure'] for x in development),'source_counts':{s:dict(Counter(x['source_id'] for x in rs)) for s,rs in selected.items()},'title_hint_counts':{s:dict(Counter(str(x['title_hint']) for x in rs)) for s,rs in selected.items()},'outputs':{s:hashlib.sha256((P/'sets'/f'{s}-unlabeled.jsonl').read_bytes()).hexdigest() for s in selected},'limitations':['Only baseline-recalled complete bodies; recall is retention within that pool, not market-wide recall.','Title strata are sampling hints, not GOLD; do not force a label percentage.','Six sources were searched but no complete Bloomberg article matched the frozen IONQ baseline.']}
    save(P/'dataset-manifest.json',manifest)
    print(json.dumps({k:manifest[k] for k in ['counts','family_overlap','validation_prior_nine_ticker_exposure','source_counts']}))

if __name__=='__main__':main()
