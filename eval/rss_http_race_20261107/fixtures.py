from pathlib import Path
import json
from lxml import html
r=Path('eval/rss_http_race_20261107'); f=Path('tests/fixtures/rss_http_race');f.mkdir(parents=True,exist_ok=True)
groups={
'unmanned-policies-html':('unmannedairspace_counteruas',0,'//article[contains(@class,"small-archive-post")]'),
'unmanned-tenders-html':('unmannedairspace_counteruas',1,'//article[contains(@class,"small-archive-post")]'),
'register-software-lean':('register_software_cloud_security',0,None),
'register-cloud-lean':('register_software_cloud_security',1,None),
'register-security-lean':('register_software_cloud_security',2,None),
'clickhouse-blog-html':('nbis_official_actor_rss',0,'//h2/a[starts-with(@href,"/blog/")]/../..|//h3/a[starts-with(@href,"/blog/")]/../../..'),
'meta-news-api':('nbis_official_actor_rss',1,None),
'airspaceforces-space-api':('airspaceforces_space',0,None),
'ans-news-html':('ans_nuclear_newswire',0,'//article[starts-with(@id,"article_")]'),
'intc-ir-html':('intc.press',0,'//article[contains(@class,"media-container")]'),
'rklb-ir-html':('rklb.ir',0,'//article[contains(@class,"node--type-nir-news")]'),
'mu-q4-json':('mu.ir',0,None),'be-q4-json':('be.press',0,None),
}
for i,c in enumerate(['big-data','cloud','security']):groups['siliconangle-'+c+'-api']=('siliconangle_software',i,None)
groups['siliconangle-ai-api']=('siliconangle_ai',0,None)
for i,c in enumerate(['topic-ai','topic-industry','department-hardware']):groups['wccftech-'+c+'-html']=('wccftech_hardware',i,'//div[contains(concat(" ",normalize-space(@class)," ")," post ")]')
for i,c in enumerate(['business','national','capital-markets']):groups['quantuminsider-'+c+'-api']=('quantum_insider',i,None)
for i,c in enumerate(['nuclear','data-centers','legal-regulatory','safety','distributed-generation','energy-storage-2','hydrogen']):groups['power-'+c+'-html']=('power_magazine_energy',i,'//time/../../..')
from doxagent.message_bus_v2.rss_http_race import RECIPES,IR_RECIPES
from doxagent.message_bus_v2.public_listing import parse_listing
from doxagent.message_bus_v2.technology_sources import TechnologySource
from urllib.parse import urlsplit
from datetime import datetime,UTC
manifest=[]
for name,(sid,index,xpath) in groups.items():
 raw=(r/(name+'.body')).read_text(encoding='utf8')
 recipe=IR_RECIPES[sid] if sid in IR_RECIPES else RECIPES[sid][index]
 spec=TechnologySource(sid,sid,urlsplit(recipe.url).hostname.removeprefix('www.'),(recipe.url,),())
 full=parse_listing(recipe,spec,raw,datetime.now(UTC))
 if xpath:
  nodes=html.fromstring(raw).xpath(xpath)[:3];small='<html><body>'+''.join(html.tostring(n,encoding='unicode') for n in nodes)+'</body></html>'
 else:
  data=json.loads(raw)
  if isinstance(data,list):data=data[:3]
  elif 'result' in data:data={'result':data['result'][:3]}
  else:data={'GetPressReleaseListResult':data['GetPressReleaseListResult'][:3]}
  small=json.dumps(data,ensure_ascii=False)
 rows=parse_listing(recipe,spec,small,datetime.now(UTC))
 assert rows,(sid,name)
 filename=sid+'-'+str(index)+('.html' if xpath else '.json');(f/filename).write_text(small,encoding='utf8')
 manifest.append(dict(source=sid,index=index,file=filename,first_url=rows[0].url,first_title=rows[0].title,first_time=rows[0].published_at.isoformat(),basis=rows[0].publication_time_basis,full_count=len(full)))
(f/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
print([(x['source'],x['index'],x['full_count']) for x in manifest])

