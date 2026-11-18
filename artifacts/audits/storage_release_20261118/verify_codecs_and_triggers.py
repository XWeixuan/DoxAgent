"""Execute bounded codec checks and the existing small trigger DDL upgrade."""
import subprocess

code = r'''
import hashlib,json,os,pathlib,sqlite3,tempfile,urllib.request
from doxagent.v2_read.content_files import ContentFiles,SINGLE_FILE
from doxagent.codex_runtime.published_storage import put_local_body,read_local_body
proof={}
with tempfile.TemporaryDirectory(prefix='storage-codec-accept-') as temporary:
    root=pathlib.Path(temporary)
    files=ContentFiles(root/'native')
    for enabled,raw in [('0',b'legacy acceptance'),('1',b'new small acceptance'*20),('1',b'large acceptance'*20000)]:
        os.environ['DOXAGENT_CONTENT_SINGLE_FILE']=enabled
        key=files.put(raw)
        assert files.read_all(key)==raw
        assert files.read(key,2,7)==raw[2:9]
        proof[key[:10]]={'size':len(raw),'single':(files._path(key)/SINGLE_FILE).is_file()}
    raw=b'published compressed acceptance'*50
    key=hashlib.sha256(raw).hexdigest()
    path=put_local_body(root,key,raw,compressed=True)
    assert read_local_body(path,key)==raw
print(json.dumps({'codec_proof':proof,'published_gzip':True}))
'''
subprocess.run(['docker','exec','-i','doxagent-v2-v2-api-1','python','-'],input=code,text=True,check=True)

import sys
if '--upgrade-triggers' in sys.argv:
    code = r'''
import json,sqlite3
from doxagent.production_v2 import check,paths
from doxagent.v2_read.artifact_registry import upgrade_native_trigger_conflicts
locations=paths(check(databases=False))
result={}
for name in ('read','research','initialization','bus','runtime'):
    with sqlite3.connect(locations[name],timeout=15) as db:
        db.execute('BEGIN IMMEDIATE')
        before=db.total_changes
        upgrade_native_trigger_conflicts(db)
        rows=db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name GLOB 'v2_artifact_*_UPDATE'").fetchall()
        result[name]={'update_triggers':len(rows),'differential':sum('artifact_id NOT IN (' in r[0] for r in rows),'row_changes':db.total_changes-before}
print(json.dumps({'native_triggers':result}))
'''
    subprocess.run(['docker','exec','-i','doxagent-v2-v2-api-1','python','-'],input=code,text=True,check=True)
