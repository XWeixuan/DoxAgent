"""Compare exact committed Python source hashes in selected running roles."""
import hashlib
import json
import subprocess

commit='e56b8697e21692e782783efc97da803fa62a42fb'
paths=[p for p in subprocess.check_output(['git','diff-tree','--no-commit-id','--name-only','-r',commit],text=True).splitlines() if p.startswith('src/')]
result={}
for role in ('site-access','message-bus','content-enrichment'):
    role_commit='f6ef40407ab7dd5871eb3a6a8c0fbf1ea65b9ea0' if role=='message-bus' else commit
    expected={p:hashlib.sha256(subprocess.check_output(['git','show',role_commit+':'+p])).hexdigest() for p in paths}
    lines=subprocess.check_output(['docker','exec','doxagent-v2-v2-'+role+'-1','sha256sum',*['/app/'+p for p in paths]],text=True)
    actual={line.split()[1].removeprefix('/app/'):line.split()[0] for line in lines.splitlines()}
    result[role]={'commit':role_commit,'matched':len([p for p,h in expected.items() if actual.get(p)==h]),
        'mismatches':[p for p,h in expected.items() if actual.get(p)!=h]}
print(json.dumps({'commit':commit,'source_count':len(paths),'roles':result}))
