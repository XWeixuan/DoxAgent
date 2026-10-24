"""Append streamed, read-only historical BBO pages; never lose completed pages."""
import json,subprocess,sys
from pathlib import Path
from datetime import datetime,timedelta,UTC
ROOT=Path(__file__).parent
start=datetime(2026,10,14,20,18,tzinfo=UTC);cursors={s:start for s in ['RKLB','QQQ']}
cache=[]
for line in (ROOT/'quote_pages.jsonl').read_text(encoding='utf8').splitlines():
    r=json.loads(line)
    if r['kind']=='quote_page' and not r.get('cached_probe') and 'response' in r:
        cursors[r['symbol']]=max(cursors[r['symbol']],datetime.fromisoformat(r['response']['events'][-1]['timestamp'])+timedelta(seconds=1))
    if r['kind']=='quote_page' and r.get('cached_probe'):cache.append(r)
plan={s:t.isoformat() for s,t in cursors.items()}
template=(ROOT/'quote_pages_remote_template.py').read_text(encoding='utf8')
code=template.replace('__CURSORS__',repr(plan)).replace('__CACHE__',repr(cache)).replace('__REQUESTS__',str(int(sys.argv[1]) if len(sys.argv)>1 else 12))
(ROOT/'quote_pages_remote.py').write_text(code,encoding='utf8')
command='sudo -n docker exec -i doxagent-v2-v2-api-1 python -'
with (ROOT/'quote_pages.jsonl').open('ab') as out,(ROOT/'quote_pages.stderr').open('ab') as err:
    result=subprocess.run(['ssh','-o','BatchMode=yes','-o','PasswordAuthentication=no','doxagent-sg',command],input=code.encode('utf8'),stdout=out,stderr=err)
print('remote exit',result.returncode)
sys.exit(result.returncode)
