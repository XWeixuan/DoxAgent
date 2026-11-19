"""Bounded file evidence for selected real Jobs; no business repository reads."""
import gzip,json,os,sys,time
from datetime import datetime,UTC
from pathlib import Path
cohort=json.load(gzip.open(sys.argv[1]))
wanted={j['id'] for c in cohort['cases'] for j in c['jobs']}
since=datetime.fromisoformat(sys.argv[2].replace('Z','+00:00')).timestamp()
root=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/workspaces/boundary-timing')
started=time.monotonic();candidates=[];checked=0;limited=False
with os.scandir(root) as entries:
    for entry in entries:
        checked+=1
        if checked>100000 or time.monotonic()-started>2:
            limited=True;break
        if not entry.name.endswith('.json'):continue
        try:
            stat=entry.stat(follow_symlinks=False)
            if stat.st_mtime>=since and 0<stat.st_size<=512000:candidates.append((stat.st_mtime,entry.path))
        except OSError:pass
traces=[];read=0
for _,path in sorted(candidates,reverse=True)[:512]:
    read+=1
    try:
        with open(path,'rb') as f:
            prefix=f.read(2048)
            if not any(identity.encode() in prefix for identity in wanted):continue
            value=json.loads(prefix+f.read())
        if value.get('job_id') in wanted:traces.append(value)
    except (OSError,ValueError):pass
print(json.dumps(dict(at=datetime.now(UTC).isoformat(),scanned=checked,candidates=len(candidates),read=read,scan_limited=limited or len(candidates)>512,traces=traces,missing=sorted(wanted-{t['job_id'] for t in traces}))))
