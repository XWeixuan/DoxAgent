import json, subprocess, urllib.parse

nodes=[]
for region,name in [("SG","新加坡"),("JP","日本"),("US","美国"),("HK","香港")]:
    for tier in ("实验性","高级","标准"):
        flag={"SG":"🇸🇬","JP":"🇯🇵","US":"🇺🇸","HK":"🇭🇰"}[region]
        nodes.append((region,tier,f"{flag} {name}{tier} IEPL 专线 1"))
for region,tier,node in nodes:
    path=urllib.parse.quote(node,safe='')
    url=f"http://127.0.0.1:9090/proxies/{path}/delay?url=https%3A%2F%2Fwww.gstatic.com%2Fgenerate_204&timeout=5000"
    p=subprocess.run(["sudo","-n","docker","run","--rm","--network","container:doxagent-egress-clash","curlimages/curl:8.12.1","-sS","--max-time","8",url],capture_output=True,text=True)
    try: result=json.loads(p.stdout)
    except Exception: result={"error":(p.stderr or p.stdout).strip()[:160]}
    print(json.dumps({"region":region,"tier":tier,"node":node,"result":result},ensure_ascii=False),flush=True)
