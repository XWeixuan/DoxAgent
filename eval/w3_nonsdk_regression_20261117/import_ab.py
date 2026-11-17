"""One disposable, network-disabled import A/B; never initializes an SDK client."""
import subprocess,json
from pathlib import Path
image='sha256:870718ba2b48075e2103fff8f2bdd3fdec92d80fb7b5c13d1648f0107f888e23'
code=r'''
import json,subprocess,os,sys,importlib.util,py_compile
from pathlib import Path
probe="""import time,sys,json
w=time.monotonic();c=time.process_time()
from doxagent.codex_worker.schema import WorkerRunRequest
from doxagent.codex_worker.sdk_runtime import OpenAICodexRuntime
print(json.dumps(dict(wall_ms=(time.monotonic()-w)*1000,cpu_ms=(time.process_time()-c)*1000,paths=sorted({m.__file__ for m in tuple(sys.modules.values()) if isinstance(getattr(m,'__file__',None),str) and m.__file__.endswith('.py')}))))
"""
a=json.loads(subprocess.check_output([sys.executable,'-c',probe]));sys.pycache_prefix='/tmp/import-ab'
for p in a['paths']:
 if Path(p).is_file():py_compile.compile(p,cfile=importlib.util.cache_from_source(p),doraise=True,invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
b=json.loads(subprocess.check_output([sys.executable,'-c',probe],env=dict(os.environ,PYTHONPYCACHEPREFIX=sys.pycache_prefix)))
print(json.dumps(dict(baseline={k:a[k] for k in ('wall_ms','cpu_ms')},compiled={k:b[k] for k in ('wall_ms','cpu_ms')},sources=len(a['paths']),sdk_calls=0)))
'''
print(subprocess.check_output(['docker','run','--rm','-i','--network','none','--memory','512m','--cpus','1','--entrypoint','python',image,'-'],input=code.encode()).decode())
