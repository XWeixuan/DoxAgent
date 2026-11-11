import subprocess,json
CODE='''import os,json
from doxagent.v2_read.repository import ReadStore
from doxagent.api_v2.article_samples import select_articles
store=ReadStore(os.environ['DOXAGENT_V2_READ_SQLITE_PATH'])
query=next(c for c in select_articles.__code__.co_consts if isinstance(c,str) and c.startswith('SELECT s.value'))
with store.connect() as db:
 plan=[tuple(r) for r in db.execute('EXPLAIN QUERY PLAN '+query,('[\"news\"]','MU','MU'))]
print(json.dumps({'plan':plan}))
'''
cid=subprocess.check_output(['docker','ps','-q','--filter','label=com.docker.compose.service=v2-api'],text=True).strip()
p=subprocess.run(['docker','exec','-i',cid,'python','-'],input=CODE,text=True,capture_output=True,timeout=10)
print(p.stdout)
if p.returncode:raise RuntimeError(p.stderr[-1000:])
