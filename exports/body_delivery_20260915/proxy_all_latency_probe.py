import concurrent.futures, json, subprocess, urllib.parse

IMAGE='curlimages/curl:8.12.1'
def api(path,timeout=8):
 p=subprocess.run(['sudo','-n','docker','run','--rm','--network','container:doxagent-egress-clash',IMAGE,
   '-sS','--max-time',str(timeout),'http://127.0.0.1:9090'+path],capture_output=True,text=True,timeout=timeout+5)
 return p
p=api('/proxies',15)
catalog=json.loads(p.stdout)['proxies']
nodes=sorted(name for name,value in catalog.items() if value.get('type')=='Shadowsocks')
def measure(name):
 path='/proxies/'+urllib.parse.quote(name,safe='')+'/delay?url=https%3A%2F%2Fwww.gstatic.com%2Fgenerate_204&timeout=4500'
 p=api(path,7)
 try: detail=json.loads(p.stdout)
 except Exception: detail={'message':(p.stderr or p.stdout).strip()[:120]}
 return {'node':name,'delay':detail.get('delay'),'message':detail.get('message')}
with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
 rows=list(pool.map(measure,nodes))
rows.sort(key=lambda x:(x['delay'] is None,x['delay'] or 999999,x['node']))
print(json.dumps({'total':len(rows),'reachable':sum(r['delay'] is not None for r in rows),'rows':rows},ensure_ascii=False))
