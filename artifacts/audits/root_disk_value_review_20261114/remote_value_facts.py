import json,os,sqlite3,time,subprocess
from pathlib import Path
from contextlib import closing
from collections import Counter
from datetime import datetime,timezone,timedelta
os.nice(10);b=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data');out={'at':datetime.now(timezone.utc).isoformat(),'facts':{},'errors':{}}
counts=Counter()
for entry in os.scandir(b/'workspaces'):
 n=entry.name
 if not entry.is_dir(follow_symlinks=False):counts['files']+=1;continue
 group=next((p for p in ['runtime-maintain-','persistent-runtime-w3-','w3-ready-','w3-case-','w3-template-','init-','d2ws-','rerun-'] if n.startswith(p)),n if n.startswith('.') else 'other')
 counts[group]+=1
out['workspace_direct_children']=dict(counts)
queries={
'workspaces/worker-jobs.sqlite3':{
 'job_status':"SELECT status,count(*) FROM jobs GROUP BY status",
 'registry':"SELECT kind,status,count(*) FROM w3_thread_registry GROUP BY kind,status",
 'active_jobs':"SELECT id,json_extract(payload,'$.run_id'),json_extract(request,'$.node') FROM jobs WHERE status IN ('queued','running')",
 'ready_states':"SELECT state,count(*) FROM w3_ready_slots GROUP BY state",
 'owners':"SELECT key,value FROM metadata WHERE key IN ('resource_retention','workspace_exit_cursor','session_retention','session_retention_deferred')"},
'runtime/runtime.sqlite3':{
 'cases':"SELECT status,technical_status,count(*) FROM runtime_v2_cases GROUP BY status,technical_status",
 'tasks':"SELECT kind,status,count(*),min(updated_at),max(updated_at) FROM runtime_tasks GROUP BY kind,status",
 'namespaces':"SELECT namespace,count(*) FROM runtime_values GROUP BY namespace",
 'native_by_state':"SELECT class,state,count(*),sum(size) FROM artifact GROUP BY class,state",
 'native_owner_types':"SELECT owner_type,count(*) FROM artifact_ref GROUP BY owner_type"},
'research/research.sqlite3':{
 'record_types':"SELECT record_type,count(*) FROM codex_runtime_records GROUP BY record_type",
 'd3_run_states':"SELECT json_extract(payload,'$.status'),json_extract(payload,'$.settlement') IS NOT NULL,json_extract(payload,'$.resource_retirement') IS NOT NULL,count(*) FROM codex_document3_v21_runs GROUP BY 1,2,3",
 'native_by_state':"SELECT class,state,count(*),sum(size) FROM artifact GROUP BY class,state",
 'native_owner_types':"SELECT owner_type,count(*) FROM artifact_ref GROUP BY owner_type"},
'codex-home/state_5.sqlite':{
 'threads':"SELECT archived,count(*),min(created_at),max(updated_at) FROM threads GROUP BY archived"},
'bus/acquisition.sqlite3':{
 'retention':"SELECT key,value FROM meta WHERE key IN ('retention','physical_exit_cursor','schedule_exit_cursor')",
 'hour_summary':"SELECT count(*),min(hour),max(hour) FROM unit_hours",
 'oldest_cycle':"SELECT dispatch_at,status FROM cycles ORDER BY dispatch_at LIMIT 1"}}
for rel,items in queries.items():
 with closing(sqlite3.connect((b/rel).as_uri()+'?mode=ro',uri=True,timeout=.2)) as db:
  db.execute('PRAGMA query_only=ON')
  for key,sql in items.items():
   started=time.monotonic();db.set_progress_handler(lambda:time.monotonic()-started>2,1000)
   try:out['facts'][rel+':'+key]=db.execute(sql).fetchall()
   except sqlite3.Error as e:out['errors'][rel+':'+key]=str(e)
p=Path('/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data/registry/site_strategy.sqlite3')
with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.2)) as db:
 db.execute('PRAGMA query_only=ON');out['site_profiles']=db.execute('SELECT profile_id,directory_key,updated_at FROM browser_profiles').fetchall()
 out['site_current_identities']=db.execute("SELECT h.identity_id,h.enabled,json_extract(r.spec_json,'$.profile_id'),json_extract(r.spec_json,'$.browser_profile_id') FROM browser_identity_heads h JOIN browser_identity_revisions r ON r.identity_id=h.identity_id AND r.revision=h.active_revision").fetchall()
print(json.dumps(out))
