import json,os,time,subprocess
from pathlib import Path
out={}
b=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/read/serving-v4-20261018')
out['read_children']=[{'name':x.name,'dir':x.is_dir(),'bytes':x.stat().st_size,'allocated':x.stat().st_blocks*512} for x in b.iterdir()]
b=Path('/home/doxagent-desktop/.config/doxagent/reuters-chrome/BrowserMetrics');a=[x.stat() for x in b.glob('*.pma') if x.is_file()];out['browser_metrics']={'count':len(a),'older48h_allocated':sum(x.st_blocks*512 for x in a if time.time()-x.st_mtime>48*3600),'min_mtime':min(x.st_mtime for x in a),'max_mtime':max(x.st_mtime for x in a)}
out['docker_containers']=subprocess.run(['docker','ps','-a','--format','{{.Names}}|{{.Status}}|{{.Image}}'],capture_output=True,text=True).stdout
out['docker_images']=subprocess.run(['docker','image','ls','--no-trunc','--format','{{.Repository}}:{{.Tag}}|{{.ID}}|{{.Size}}'],capture_output=True,text=True).stdout
out['df']=subprocess.run(['df','-B1','/'],capture_output=True,text=True).stdout
print(json.dumps(out))
