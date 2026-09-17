import json
import subprocess
import time

NODES = {
    "JP_EXP": "🇯🇵 日本实验性 IEPL 专线 1",
    "JP_STD": "🇯🇵 日本标准 IEPL 专线 1",
    "US_EXP": "🇺🇸 美国实验性 IEPL 专线 1",
    "US_ADV": "🇺🇸 美国高级 IEPL 专线 1",
}
URLS = {
    "thestreet": "https://www.thestreet.com/investing/billionaire-david-tepper-just-dumped-a-red-hot-ai-stock",
    "247wallst": "https://247wallst.com/investing/2026/08/31/apple-just-raised-mac-and-ipad-prices-20-so-who-is-getting-rich-off-the-shortage/",
    "yahoo_article": "https://finance.yahoo.com/markets/stocks/articles/3-great-quality-stocks-own-201414086.html",
    "investopedia": "https://www.investopedia.com/market-update-memory-other-ai-related-stocks-lead-the-markets-top-performers-friday-12108315",
    "yahoo_api": "https://query1.finance.yahoo.com/v1/finance/search?q=MU&quotesCount=1&newsCount=3",
    "reuters_search": "https://www.reuters.com/site-search/?query=Micron&offset=0",
}
IMAGE = "curlimages/curl:8.12.1"

def run(args, timeout=35):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)

def select(node):
    payload = json.dumps({"name": node}, ensure_ascii=False)
    result = run(["sudo", "-n", "docker", "run", "--rm", "--network", "container:doxagent-egress-clash", IMAGE,
        "-fsS", "-X", "PUT", "-H", "Content-Type: application/json", "--data", payload,
        "http://127.0.0.1:9090/proxies/GLOBAL"])
    if result.returncode:
        raise RuntimeError(result.stderr.strip())

def fetch(url, proxy=True):
    cmd = ["sudo", "-n", "docker", "run", "--rm", "--network", "doxagent-v2_default", IMAGE,
        "-L", "--max-redirs", "5", "--connect-timeout", "8", "--max-time", "25",
        "-A", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36",
        "-o", "/dev/null", "-sS", "-w", "%{http_code}\t%{size_download}\t%{url_effective}\t%{remote_ip}"]
    if proxy:
        cmd += ["-x", "http://doxagent-egress-clash:7893"]
    result = run(cmd + [url])
    parts = result.stdout.strip().split("\t")
    return {"status": int(parts[0]) if parts and parts[0].isdigit() else 0,
            "bytes": int(parts[1]) if len(parts)>1 and parts[1].isdigit() else 0,
            "final_url": parts[2] if len(parts)>2 else "", "remote_ip": parts[3] if len(parts)>3 else "",
            "curl_exit": result.returncode, "error": result.stderr.strip()[:160]}

rows=[]
for region,node in NODES.items():
    select(node)
    time.sleep(1)
    ip=run(["sudo","-n","docker","run","--rm","--network","doxagent-v2_default",IMAGE,"-fsS","-x","http://doxagent-egress-clash:7893","--max-time","15","https://api.ipify.org"]).stdout.strip()
    for key,url in URLS.items():
        result=fetch(url)
        rows.append({"region":region,"node":node,"exit_ip":ip,"case":key,**result})
        print(region,key,result["status"],result["bytes"],flush=True)
        time.sleep(.8)
print("RESULT_JSON="+json.dumps(rows,ensure_ascii=False))
