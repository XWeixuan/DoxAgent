"""Observe only newly closed timing files and scalar cgroup counters; no DB I/O."""
import ctypes
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import select
import struct
import subprocess
import sys
import time

os.nice(19)
root=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
libc=ctypes.CDLL(None,use_errno=True)
fd=libc.inotify_init1(os.O_NONBLOCK|os.O_CLOEXEC)
if fd<0:raise OSError(ctypes.get_errno(),'inotify_init1')
watches={}
for folder in [root/'runtime/boundary-timing',root/'workspaces/boundary-timing']:
    wd=libc.inotify_add_watch(fd,os.fsencode(folder),0x8|0x80)
    if wd>=0:watches[wd]=folder
roles={}
for role in ['v2-scheduler','codex-worker','v2-initialization','v2-cdecr-executor']:
    v=json.loads(subprocess.check_output(['docker','inspect','doxagent-v2-'+role+'-1']))[0]
    cgroup=Path('/proc')/str(v['State']['Pid'])/'cgroup'
    location=cgroup.read_text().split('0::',1)[1].strip()
    roles[role]=dict(path=Path('/sys/fs/cgroup'+location),image=v['Image'],started=v['State']['StartedAt'])
out={'started_at':datetime.now(UTC).isoformat(),'roles':{k:{a:b for a,b in v.items() if a!='path'} for k,v in roles.items()},
     'traces':[],'samples':[],'bytes_read':0,'dropped':0}
started=time.monotonic();next_sample=0
try:
    while time.monotonic()-started<float(sys.argv[2]):
        if time.monotonic()>=next_sample:
            sample={'at':datetime.now(UTC).isoformat(),'roles':{},'pressure':{}}
            for name in ['cpu','memory','io']:
                sample['pressure'][name]=(Path('/proc/pressure')/name).read_text()
            mem=dict(line.split(':',1) for line in Path('/proc/meminfo').read_text().splitlines())
            sample['mem_available_kib']=int(mem['MemAvailable'].split()[0])
            sample['vmstat']={k:int(v) for k,v in (line.split() for line in Path('/proc/vmstat').read_text().splitlines()) if k in {'pswpin','pswpout','oom_kill'}}
            for role,info in roles.items():
                sample['roles'][role]={}
                for name in ['memory.current','memory.swap.current','memory.events','cpu.stat','cpu.pressure','io.stat','io.pressure','pids.current']:
                    try:sample['roles'][role][name]=(info['path']/name).read_text()
                    except OSError:pass
            out['samples'].append(sample);next_sample=time.monotonic()+15
        if not select.select([fd],[],[],min(1,max(0,started+float(sys.argv[2])-time.monotonic())))[0]:continue
        data=os.read(fd,65536);offset=0
        while offset+16<=len(data):
            wd,mask,cookie,length=struct.unpack_from('iIII',data,offset);offset+=16
            name=data[offset:offset+length].split(b'\0',1)[0];offset+=length
            if wd not in watches or not name.endswith(b'.json'):continue
            if len(out['traces'])>=200 or out['bytes_read']>=2*1024**2:
                out['dropped']+=1;continue
            try:
                path=watches[wd]/os.fsdecode(name)
                if path.stat().st_size>256*1024:continue
                raw=path.read_bytes();out['bytes_read']+=len(raw)
                v=json.loads(raw)
                if 'totals' in v:out['traces'].append(v|{'observed_directory':str(watches[wd])})
            except (OSError,ValueError):pass
finally:os.close(fd)
out['finished_at']=datetime.now(UTC).isoformat()
Path(sys.argv[1]).write_text(json.dumps(out,separators=(',',':')))
print(json.dumps({'samples':len(out['samples']),'traces':len(out['traces']),'bytes_read':out['bytes_read'],'dropped':out['dropped']}))
