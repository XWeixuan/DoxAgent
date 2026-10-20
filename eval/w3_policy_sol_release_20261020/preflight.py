import json,subprocess,shutil
from pathlib import Path
def run(*args):return subprocess.check_output(args,text=True).strip()
repo=Path('/home/ubuntu/doxagent');root=Path('/var/lib/doxagent/releases')
containers=json.loads(run('docker','inspect','doxagent-v2-codex-worker-1','doxagent-v2-v2-scheduler-1'))
out={'repo_commit':run('git','-C',str(repo),'rev-parse','HEAD'),'repo_status':run('git','-C',str(repo),'status','--short'),'disk_free':shutil.disk_usage('/').free,'containers':[]}
for c in containers:
 env=dict(v.split('=',1) for v in c['Config']['Env'])
 out['containers'].append({'name':c['Name'],'image':c['Image'],'tag':c['Config']['Image'],'release':env.get('DOXAGENT_BUILD_COMMIT'),'state':c['State']['Status'],'health':c['State'].get('Health',{}).get('Status'),'service':c['Config']['Labels'].get('com.docker.compose.service')})
state=json.loads((root/'state.json').read_text());out['release_components']={k:{x:v for x,v in value.get('current',{}).items() if x in ['commit','image','tag']} for k,value in state.get('components',{}).items() if k in ['codex-worker','v2-scheduler']}
code='''import json,sqlite3,os
from pathlib import Path
db=sqlite3.connect('file:/data/workspaces/worker-jobs.sqlite3?mode=ro',uri=True)
rows=db.execute("SELECT id,status,json_extract(request,'$.task_phase'),json_extract(request,'$.model'),json_extract(payload,'$.started_at') FROM jobs WHERE status IN ('queued','running')").fetchall()
import tomllib
p=Path(os.environ['CODEX_HOME'])/'config.toml';cfg=tomllib.loads(p.read_text()) if p.exists() else {}
print(json.dumps({'active_jobs':rows,'service_tier':cfg.get('service_tier'),'profile_tiers':{k:v.get('service_tier') for k,v in cfg.get('profiles',{}).items()}}))
'''
out['worker']=json.loads(subprocess.check_output(['docker','exec','-i','doxagent-v2-codex-worker-1','python','-'],input=code,text=True))
print(json.dumps(out))
