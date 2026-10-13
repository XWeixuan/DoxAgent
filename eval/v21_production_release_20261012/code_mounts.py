import json,subprocess
x=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-v2-api-1']))[0]
print('api mounts',[(m['Destination'],m['Source']) for m in x['Mounts']])
b=json.loads(open('/home/ubuntu/v21-production-20261012/private-container-baseline.json').read())
for n,c in b.items():
 m=[(x['Destination'],x['Source']) for x in c['Mounts'] if x['Destination'].startswith('/app')]
 if m: print(n,m)
