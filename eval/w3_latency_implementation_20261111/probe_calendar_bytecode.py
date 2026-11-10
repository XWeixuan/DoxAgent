"""Disposable pyc comparison for the observed calendar cold path; no DB or SDK."""
import importlib.util
import json
import os
from pathlib import Path
import py_compile
import signal
import subprocess
import sys
import tempfile

os.nice(19);signal.alarm(45)
code='''
import time,json,sys
a=time.monotonic();p=time.process_time()
import exchange_calendars
b=time.monotonic();q=time.process_time()
cal=exchange_calendars.get_calendar('XNYS',start='2025-01-01',end='2027-12-31')
c=time.monotonic();r=time.process_time()
paths=sorted({m.__file__ for m in tuple(sys.modules.values()) if isinstance(getattr(m,'__file__',None),str) and m.__file__.endswith('.py')})
print(json.dumps(dict(import_ms=(b-a)*1000,import_cpu_ms=(q-p)*1000,build_ms=(c-b)*1000,build_cpu_ms=(r-q)*1000,close_at=cal.session_close('2026-11-11').isoformat(),sources=paths)))
'''
baseline=json.loads(subprocess.check_output([sys.executable,'-c',code]));sources=baseline.pop('sources')
with tempfile.TemporaryDirectory(prefix='w3-calendar-pyc-') as directory:
    saved=sys.pycache_prefix;sys.pycache_prefix=directory
    try:
        for source in sources:
            if Path(source).is_file():
                target=importlib.util.cache_from_source(source);Path(target).parent.mkdir(parents=True,exist_ok=True)
                py_compile.compile(source,cfile=target,doraise=True,invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)
    finally:sys.pycache_prefix=saved
    cached=json.loads(subprocess.check_output([sys.executable,'-X','pycache_prefix='+directory,'-c',code]));cached.pop('sources')
    size=sum(p.stat().st_size for p in Path(directory).rglob('*.pyc'))
print(json.dumps(dict(baseline=baseline,compiled=cached,temporary_pyc_bytes=size,imported_sources=len(sources),model_calls=0,production_writes=0)))
