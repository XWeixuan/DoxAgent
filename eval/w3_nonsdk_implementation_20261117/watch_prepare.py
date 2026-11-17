"""Ephemeral inotify observer: new small timing receipts only, never a tree scan."""
import ctypes
import json
import os
from pathlib import Path
import select
import stat
import struct
import time
from datetime import datetime,UTC

root=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data/workspaces/boundary-timing')
lib=ctypes.CDLL(None,use_errno=True)
fd=lib.inotify_init1(os.O_NONBLOCK|os.O_CLOEXEC)
if fd<0:raise OSError(ctypes.get_errno(),'inotify_init1')
watch=lib.inotify_add_watch(fd,os.fsencode(root),0x8|0x80)
if watch<0:os.close(fd);raise OSError(ctypes.get_errno(),'inotify_add_watch')
out=dict(started_at=datetime.now(UTC).isoformat(),receipts=[],events=0,overflow=False)
end=time.monotonic()+150
try:
    while time.monotonic()<end and out['events']<256:
        if not select.select([fd],[],[],min(1,end-time.monotonic()))[0]:continue
        raw=os.read(fd,65536);offset=0
        while offset<len(raw):
            wd,mask,cookie,length=struct.unpack_from('iIII',raw,offset)
            name=os.fsdecode(raw[offset+16:offset+16+length].split(b'\0',1)[0]);offset+=16+length
            if mask&0x4000:out['overflow']=True
            if not name.endswith('.json') or Path(name).name!=name:continue
            path=root/name
            try:
                size=path.lstat()
                if not stat.S_ISREG(size.st_mode) or size.st_size>131072:continue
                value=json.loads(path.read_text())
            except (OSError,ValueError):continue
            out['events']+=1
            if value.get('plane')!='capsule_prepare':continue
            out['receipts'].append({k:value.get(k) for k in ('trace_id','plane','run_id','started_at','finished_at','elapsed_ms','totals','events')})
finally:os.close(fd)
out['finished_at']=datetime.now(UTC).isoformat()
print(json.dumps(out))
