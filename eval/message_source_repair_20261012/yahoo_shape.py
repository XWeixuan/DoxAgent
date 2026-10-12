"""Inspect only public-news hydration shapes through the existing owner."""
import json,httpx
from lxml import html
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.tokens import read_token
s=DoxAgentSettings()
with httpx.Client(base_url='http://127.0.0.1:8011',timeout=65,headers={'Authorization':'Bearer '+read_token(s.site_access_worker_token,s.site_access_worker_token_file)}) as c:
    v=c.post('/v1/access/execute',json={'operation_id':'yahoo-shape-20261012','purpose':'CRAWLER','mode':'BROWSER','url':'https://finance.yahoo.com/quote/MU/latest-news/','remaining_budget_ms':45000}).json()
tree=html.fromstring(v.get('body') or '<html/>')
out={'disposition':v.get('disposition'),'scripts':[]}
links=tree.xpath('//a[contains(@href,"/news/")][.//h3 or .//h2]')
out['article_links']=[{'url':a.get('href'), 'text':a.text_content()[:200],
                        'parent':html.tostring(a.getparent(),encoding='unicode')[:1800]} for a in links[:3]]
out['article_count']=len(links)
body=v.get('body','')
out['markers']={marker:body.find(marker) for marker in ('pubDate','latestNews','tickerStream','newsStream','streamItems','data-testid="storyitem"')}
for el in tree.xpath('//script[@data-sveltekit-fetched]'):
    url=el.get('data-url','')
    if not any(x in url for x in ('/xhr/cds','/xhr/ncp')): continue
    row={'url':url.split('?')[0]}
    try:
        env=json.loads(el.text);p=json.loads(env.get('body','{}'))
        row['status']=env.get('status'); row['root_type']=type(p).__name__;row['keys']=list(p)[:12] if isinstance(p,dict) else None
        row['shapes']=[]; row['news_samples']=[]
        def walk(x,path='',depth=0):
            if depth>10:return
            if isinstance(x,dict):
                if any(k in x for k in ('title','pubDate','published_at','content','stream')):
                    row['shapes'].append({'path':path,'keys':list(x)[:16]})
                if 'title' in x and any(k in x for k in ('pubDate','canonicalUrl','clickThroughUrl','published_at')):
                    row['news_samples'].append({k:x.get(k) for k in ('title','pubDate','canonicalUrl','clickThroughUrl','summary','provider') if k in x})
                for k,v in x.items():walk(v,path+'.'+k,depth+1)
            elif isinstance(x,list):
                for i,v in enumerate(x[:3]):walk(v,path+f'[{i}]',depth+1)
        walk(p);row['shapes']=row['shapes'][:15];row['news_samples']=row['news_samples'][:2]
    except Exception as exc:row['error']=type(exc).__name__
    out['scripts'].append(row)
print(json.dumps(out,ensure_ascii=False))
