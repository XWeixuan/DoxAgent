import json,subprocess
from pathlib import Path
root=Path('/home/ubuntu/v21-production-20261012')
container='doxagent-v2-v2-initialization-1'
subprocess.run(['docker','cp',str(root/'ops/adoption-manifest.json'),container+':/tmp/v21-adoption-manifest.json'],check=True)
for ticker in ('MU','BE','INTC'):
 result=subprocess.run(['docker','exec',container,'python','-m','doxagent.research_adoption','activate','--manifest','/tmp/v21-adoption-manifest.json','--ticker',ticker],text=True,capture_output=True,check=True)
 (root/('activation-submit-'+ticker+'.json')).write_text(result.stdout)
 d=json.loads(result.stdout);print(json.dumps(dict(ticker=ticker,initialization_id=d.get('initialization_id'),status=d.get('status'),already_active=d.get('already_active'))),flush=True)
