import json, subprocess, time

IMAGE='curlimages/curl:8.12.1'
NODES=[
 ('JP_STD6','🇯🇵 日本标准 IEPL 专线 6'),('JP_ADV1','🇯🇵 日本高级 IEPL 专线 1'),
 ('KR1','🇰🇷 韩国标准 IEPL 专线 1'),('DE1','🇩🇪 德国标准 IEPL 专线 1'),
 ('NL2','🇳🇱 荷兰标准 IEPL 专线 2'),('FR1','🇫🇷 法国标准 IEPL 专线 1'),
 ('UK2','🇬🇧 英国标准 IEPL 专线 2'),('US_ADV1','🇺🇸 美国高级 IEPL 专线 1'),
 ('US_STD8','🇺🇸 美国标准 IEPL 专线 8'),('US_EXP1','🇺🇸 美国实验性 IEPL 专线 1'),
 ('CA2','🇨🇦 加拿大标准 IEPL 专线 2'),('IL1','🇮🇱 以色列标准 IEPL 专线 1'),
 ('BR1','🇧🇷 巴西标准 IEPL 专线 1'),('CL1','🇨🇱 智利标准 IEPL 专线 1'),
 ('AR1','🇦🇷 阿根廷标准 IEPL 专线 1')]
URLS={
 'thestreet':'https://www.thestreet.com/investing/billionaire-david-tepper-just-dumped-a-red-hot-ai-stock',
 '247wallst':'https://247wallst.com/investing/2026/08/31/apple-just-raised-mac-and-ipad-prices-20-so-who-is-getting-rich-off-the-shortage/',
 'yahoo':'https://finance.yahoo.com/markets/stocks/articles/3-great-quality-stocks-own-201414086.html',
 'investopedia':'https://www.investopedia.com/market-update-memory-other-ai-related-stocks-lead-the-markets-top-performers-friday-12108315',
 'barchart':'https://www.barchart.com/story/news/4461422/dells-monster-quarter-just-confirmed-microns-biggest-opportunity-is-not-just-hbm',
 'fool':'https://www.fool.com/investing/2026/08/22/alphabet-and-amazon-are-investing-420-billion-in-a/',
 'investorhub':'https://investorshub.advfn.com/market-news/article/35410/micron-shares-fall-1-8-following-taiwan-incentive-pay-report',
 'benzinga':'https://www.benzinga.com/markets/options/26/09/61584569/10-information-technology-stocks-whale-alerts-today-s-session',
 'reuters':'https://www.reuters.com/site-search/?query=Micron&offset=0'}
def run(args,timeout=30):return subprocess.run(args,capture_output=True,text=True,timeout=timeout)
def select(node):
 return run(['sudo','-n','docker','run','--rm','--network','container:doxagent-egress-clash',IMAGE,
  '-fsS','-X','PUT','-H','Content-Type: application/json','--data',json.dumps({'name':node},ensure_ascii=False),
  'http://127.0.0.1:9090/proxies/GLOBAL'])
def fetch(url):
 p=run(['sudo','-n','docker','run','--rm','--network','doxagent-v2_default',IMAGE,'-L','--max-redirs','5',
  '--connect-timeout','6','--max-time','18','-A','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36',
  '-o','/dev/null','-sS','-w','%{http_code}\t%{size_download}\t%{time_total}',
  '-x','http://doxagent-egress-clash:7893',url])
 parts=p.stdout.strip().split('\t')
 return {'status':int(parts[0]) if parts and parts[0].isdigit() else 0,
   'bytes':int(parts[1]) if len(parts)>1 and parts[1].isdigit() else 0,
   'seconds':float(parts[2]) if len(parts)>2 else None,'curl_exit':p.returncode,
   'error':p.stderr.strip()[:120]}
rows=[]
for alias,node in NODES:
 if select(node).returncode: continue
 for site,url in URLS.items():
  r=fetch(url);rows.append({'alias':alias,'node':node,'site':site,**r})
  print(alias,site,r['status'],r['bytes'],flush=True);time.sleep(.25)
open('/tmp/proxy_site_matrix.json','w').write(json.dumps(rows,ensure_ascii=False,indent=2))
