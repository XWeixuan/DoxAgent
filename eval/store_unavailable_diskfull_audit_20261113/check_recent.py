import subprocess,json
code='''import json
from pathlib import Path
from doxagent.v2_read.repository import ReadStore
from doxagent.v2_read.outbox import SourceOutbox
from doxagent.v2_read.formal import FormalProjectors
from doxagent.v2_read.artifacts import PublishedArtifacts
store=ReadStore(Path('/data/read/serving-v4-20261018/v2.sqlite3'))
s=SourceOutbox(Path('/data/runtime/runtime.sqlite3'),'runtime')
m=FormalProjectors(store,PublishedArtifacts(Path('/data/research/research.sqlite3')),Path('/data/events'))
for n in [4624987,4624988]:
 e=s.event(n);e['source']='runtime'
 try:
  records,metrics=m(e);print(json.dumps({'event':n,'table':e['table_name'],'records':len(records)}))
 except Exception as exc:print(json.dumps({'event':n,'table':e['table_name'],'error':str(exc),'type':type(exc).__name__}))
'''
p=subprocess.run(['docker','exec','-i','doxagent-v2-v2-projector-1','python','-'],input=code,text=True,capture_output=True,timeout=30);print(p.stdout);print(p.stderr)
