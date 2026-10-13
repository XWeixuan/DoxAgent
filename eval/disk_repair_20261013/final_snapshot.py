"""Small live acceptance receipt: no credentials, payload export or large scans."""
import hashlib
import json
import shutil
import sqlite3
import subprocess
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

DATA=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
WORK=Path('/home/ubuntu/disk-repair-20261013')

def output(args):
    return subprocess.check_output(args,text=True)

containers=json.loads(output(['docker','inspect',*output(['docker','ps','-aq']).split()]))
report=dict(at=datetime.now(UTC).isoformat(),disk=shutil.disk_usage(DATA)._asdict(),containers=[],databases={})
for container in containers:
    state=container['State']
    if not state['Running'] and 'migrate' not in container['Name']:
        continue
    entry=dict(name=container['Name'],image=container['Image'],tag=container['Config']['Image'],
               running=state['Running'],health=state.get('Health',{}).get('Status'),
               started=state['StartedAt'],finished=state['FinishedAt'],logging=container['HostConfig']['LogConfig'])
    if container['Name'] in ['/doxagent-v2-v2-api-1','/doxagent-v2-v2-projector-1']:
        start=datetime.fromisoformat(state['StartedAt'].replace('Z','+00:00'))
        stop=datetime.fromisoformat(state['FinishedAt'].replace('Z','+00:00'))
        entry['last_pause_seconds']=(start-stop).total_seconds()
    report['containers'].append(entry)
for name,relative in [('read','read/v2.sqlite3'),('bus','bus/bus.sqlite3'),('runtime','runtime/runtime.sqlite3')]:
    path=DATA/relative
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        item=dict(bytes=path.stat().st_size,pages=db.execute('PRAGMA page_count').fetchone()[0],
                  free_pages=db.execute('PRAGMA freelist_count').fetchone()[0])
        if name=='read':
            item.update(head=db.execute('SELECT max(seq) FROM commits').fetchone()[0],
                        positions=db.execute('SELECT * FROM checkpoints ORDER BY source').fetchall(),
                        gaps=db.execute('SELECT source,event,reason FROM gaps ORDER BY source,event').fetchall())
        else:
            item['source']=db.execute('SELECT * FROM v2_source_state').fetchall()
            item['pins']=db.execute('SELECT * FROM v2_source_pins').fetchall()
        if name=='bus':
            item['stable_delivery_keys']=db.execute('SELECT count(*),max(seen_count),sum(seen_count) FROM distribution_delivery_keys').fetchone()
            item['published']=db.execute("SELECT count(*) FROM distribution_deliveries WHERE state='PUBLISHED'").fetchone()[0]
        report['databases'][name]=item
report['maintenance']={}
for name in ['v2-maintenance','v2-transport-maintenance']:
    result=subprocess.run(['docker','logs','--tail','2','doxagent-v2-'+name+'-1'],capture_output=True,text=True)
    report['maintenance'][name]=(result.stdout+result.stderr).strip().splitlines()
report['p4_code_sha256']={}
for name,files in [
    ('doxagent-v2-v2-transport-maintenance-1',['production_v2.py','v2_read/backup_retention.py','v2_read/maintenance.py','v2_read/cli.py']),
    ('doxagent-v2-initialization-guardian-1',['initialization_repair/containers.py','source_maintenance/containers.py']),
]:
    paths=['/app/src/doxagent/'+file for file in files]
    code='import hashlib,json,pathlib; print(json.dumps({p:hashlib.sha256(pathlib.Path(p).read_bytes()).hexdigest() for p in '+repr(paths)+'}))'
    report['p4_code_sha256'][name]=json.loads(output(['docker','exec',name,'python','-c',code]))
(WORK/'final-snapshot.json').write_text(json.dumps(report,indent=2))
print(json.dumps(report),flush=True)
