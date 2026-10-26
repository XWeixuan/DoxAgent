"""Final body-free data, component, identity and host health receipt."""
import importlib.util
import json
import sqlite3
import subprocess
import time
from datetime import UTC,datetime,timedelta
from pathlib import Path

root=Path('/var/lib/doxagent/releases')
base=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
spec=importlib.util.spec_from_file_location('ops','/home/ubuntu/doxagent/scripts/ops/storage_release.py')
ops=importlib.util.module_from_spec(spec);spec.loader.exec_module(ops)
state=json.loads((root/'state.json').read_text())
roles={}
for c in ops.inspect_containers():
    role=c['Config'].get('Labels',{}).get('com.docker.compose.service')
    if role:
        roles[role]=dict(id=c['Id'][:12],image=c['Image'],running=c['State']['Running'],
            restarts=c['RestartCount'],started=c['State']['StartedAt'],
            health=c['State'].get('Health',{}).get('Status'),
            commit=state.get('components',{}).get(role,{}).get('current',{}).get('commit'),
            nano_cpus=c['HostConfig'].get('NanoCpus'))
now=datetime.now(UTC)
with sqlite3.connect((base/'bus/bus.sqlite3').as_uri()+'?mode=ro',uri=True,timeout=3) as db:
    start=time.monotonic();db.set_progress_handler(lambda:int(time.monotonic()-start>8),10000)
    captures=db.execute('SELECT ticker,count(*),max(observed_at) FROM poll_captures WHERE observed_at>=? GROUP BY ticker',
        ((now-timedelta(minutes=5)).isoformat(),)).fetchall()
    published=db.execute('SELECT ticker,count(*),max(published_at) FROM stream_items WHERE published_at>=? GROUP BY ticker',
        ((now-timedelta(hours=2)).isoformat(),)).fetchall()
    bodies=db.execute("SELECT status,json_extract(data_json,'$.outcome'),count(*) FROM article_body_results WHERE json_extract(data_json,'$.first_observed_at')>=? GROUP BY status,json_extract(data_json,'$.outcome')",
        ((now-timedelta(hours=2)).isoformat(),)).fetchall()
    source_count=db.execute('SELECT count(*) FROM source_definitions WHERE enabled=1').fetchone()[0]
alias=json.loads((base/'read/active.json').read_text());path=Path(alias['path'])
path=base/path.relative_to('/data') if path.is_absolute() else base/'read'/path
with sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=3) as db:
    health=db.execute('SELECT * FROM source_health').fetchall()
    gaps=db.execute('SELECT source,reason,count(*) FROM gaps GROUP BY source,reason').fetchall()
    latest=db.execute("SELECT ticker,max(sort_key) FROM object_current WHERE kind='message' GROUP BY ticker").fetchall()
    seq=db.execute('SELECT max(seq) FROM commits').fetchone()[0]
    metrics=db.execute("SELECT ticker,day,value FROM metric_buckets_current WHERE metric='messages' AND day='2026-10-26'").fetchall()
site=Path('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3')
with sqlite3.connect(site.as_uri()+'?mode=ro',uri=True,timeout=3) as db:
    identities=db.execute('SELECT identity_id,active_revision FROM browser_identity_heads ORDER BY identity_id').fetchall()
    targets=db.execute("SELECT allocation_id,target_id,owner_kind FROM owned_browser_targets WHERE owner_kind!='BUSINESS' AND state NOT IN ('CLOSED','LOST')").fetchall()
before=json.loads(Path('/var/lib/doxagent/bus-collection-20261026/production-recovery-before.json').read_text())
priorids=[[x['identity_id'],x['active_revision']] for x in before['identity_revisions']]
supervisor_before=before['roles']['v2-chrome-supervisor']
mem={x.split(':')[0]:x.split(':')[1].strip() for x in Path('/proc/meminfo').read_text().splitlines()
     if x.startswith(('MemTotal:','MemAvailable:','SwapFree:'))}
result=dict(at=now.isoformat(),roles=roles,cpus=__import__('os').cpu_count(),memory=mem,
    cpu_pressure=Path('/proc/pressure/cpu').read_text(),enabled_sources=source_count,
    captures_last_5_minutes=captures,publications_last_2_hours=published,bodies_last_2_hours=bodies,
    read_seq=seq,read_health=health,gaps=gaps,read_latest=latest,message_metrics_today=metrics,
    identity_revisions_unchanged=all(dict(identities).get(identity)==revision for identity,revision in priorids),
    identity_revision_changes=[dict(identity=identity,before=dict(priorids).get(identity),after=revision)
        for identity,revision in identities if dict(priorids).get(identity)!=revision],
    supervisor_before=supervisor_before['id'][:12],supervisor_after=roles['v2-chrome-supervisor']['id'],
    protected_targets_before=before['protected_targets'],protected_targets_after=targets)
print(json.dumps(result))
