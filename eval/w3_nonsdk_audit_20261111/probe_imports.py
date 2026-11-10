"""No SDK construction/calls. Compare import CPU using disposable external pyc files."""
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import signal
import subprocess
import sys
import tempfile

os.nice(19)
signal.alarm(45)
code='''
import time,json,sys,os,importlib.util
a=time.monotonic();c=time.process_time()
from doxagent.codex_worker.schema import WorkerRunRequest
b=time.monotonic();d=time.process_time()
from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime
e=time.monotonic();f=time.process_time()
paths=sorted({str(getattr(m,'__file__','')) for m in list(sys.modules.values()) if str(getattr(m,'__file__','')).endswith('.py')})
apps=[p for p in paths if p.startswith('/app/')]
print(json.dumps({'schema_wall_s':b-a,'schema_cpu_s':d-c,'sdk_wall_s':e-b,'sdk_cpu_s':f-d,'loaded_app_sources':len(apps),'existing_app_bytecode':sum(os.path.exists(importlib.util.cache_from_source(p)) for p in apps),'sources':paths}))
'''
baseline=json.loads(subprocess.check_output([sys.executable,'-c',code]))
with tempfile.TemporaryDirectory(prefix='w3-audit-pyc-') as cache:
    compiled=0
    for source in baseline['sources']:
        target=str(Path(cache)/source.lstrip('/'))+'c'
        Path(target).parent.mkdir(parents=True,exist_ok=True)
        py_compile.compile(source,cfile=target,doraise=True);compiled+=1
    # cache_from_source with this prefix uses /prefix/absolute/path/name.cpython-3xx.pyc.
    # Replace the simple temporary names with the interpreter's actual cache paths.
    saved=sys.pycache_prefix;sys.pycache_prefix=cache
    for source in baseline['sources']:
        old=Path(str(Path(cache)/source.lstrip('/'))+'c')
        new=Path(importlib.util.cache_from_source(source));new.parent.mkdir(parents=True,exist_ok=True);old.replace(new)
    sys.pycache_prefix=saved
    cached=json.loads(subprocess.check_output([sys.executable,'-X','pycache_prefix='+cache,'-c',code]))
    bytes_written=sum(p.stat().st_size for p in Path(cache).rglob('*.pyc'))
baseline.pop('sources');cached.pop('sources')
print(json.dumps({'baseline':baseline,'cached':cached,'compiled_files':compiled,'temporary_bytes':bytes_written,'sdk_constructed':False,'model_calls':0}))
