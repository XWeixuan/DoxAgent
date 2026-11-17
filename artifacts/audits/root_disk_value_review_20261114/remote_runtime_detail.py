import os,json,collections,sqlite3,time
from pathlib import Path
os.nice(10);r=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/runtime');out={}
p=r/'runtime_artifacts';out['run_directory_count']=sum(1 for x in p.iterdir() if x.is_dir());files=[]
for x in p.glob('*/events/US/*/event_library.sqlite3'):
 s=x.stat();files.append({'path':str(x),'bytes':s.st_size,'allocated':s.st_blocks*512})
out['event_db_count']=len(files);out['event_db_allocated']=sum(x['allocated'] for x in files);out['largest']=sorted(files,key=lambda x:-x['allocated'])[:15]
for x in [p/'last-resource-exit.json',r/'last-resource-exit.json']:
 if x.is_file():
  try:out[str(x)]=json.loads(x.read_text())
  except Exception as e:out[str(x)]=str(e)
out['runtime_root_files']=[{'name':x.name,'bytes':x.stat().st_size,'allocated':x.stat().st_blocks*512} for x in r.iterdir() if x.is_file()]
print(json.dumps(out))
