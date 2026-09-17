import json, subprocess

IMAGE='curlimages/curl:8.12.1'
NODES={
 'JP_EXP':'🇯🇵 日本实验性 IEPL 专线 1','JP_STD':'🇯🇵 日本标准 IEPL 专线 1',
 'US_EXP':'🇺🇸 美国实验性 IEPL 专线 1','US_ADV':'🇺🇸 美国高级 IEPL 专线 1'}
URLS={
 'barchart':'https://www.barchart.com/story/news/4461422/dells-monster-quarter-just-confirmed-microns-biggest-opportunity-is-not-just-hbm',
 'fool':'https://www.fool.com/investing/2026/08/22/alphabet-and-amazon-are-investing-420-billion-in-a/',
 'thestreet':'https://www.thestreet.com/investing/billionaire-david-tepper-just-dumped-a-red-hot-ai-stock'}
def run(args): return subprocess.run(args,capture_output=True,text=True,timeout=30)
def select(node):
 p=run(['sudo','-n','docker','run','--rm','--network','container:doxagent-egress-clash',IMAGE,
   '-fsS','-X','PUT','-H','Content-Type: application/json','--data',json.dumps({'name':node},ensure_ascii=False),
   'http://127.0.0.1:9090/proxies/GLOBAL'])
 if p.returncode: raise RuntimeError(p.stderr)
def fetch(url):
 p=run(['sudo','-n','docker','run','--rm','--network','doxagent-v2_default',IMAGE,'-L','--connect-timeout','8','--max-time','25',
   '-A','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36',
   '-o','/dev/null','-sS','-w','%{http_code}\t%{size_download}','-x','http://doxagent-egress-clash:7893',url])
 return p.stdout.strip(),p.returncode,p.stderr.strip()[:120]
for region,node in NODES.items():
 select(node)
 for case,url in URLS.items(): print(region,case,*fetch(url),flush=True)
