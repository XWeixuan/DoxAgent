"""One physical immutable adoption file per content and access mode."""
import hashlib
import os
from pathlib import Path
import time
from uuid import uuid4


def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()


def adopt_file(source,destination,cache):
    source,destination,cache=map(Path,(source,destination,cache))
    if source.is_symlink() or destination.is_symlink():raise ValueError('linked adoption evidence input')
    before=source.stat();sha=digest(source)
    if (source.stat().st_size,source.stat().st_mtime_ns)!=(before.st_size,before.st_mtime_ns):raise ValueError('adoption source changed')
    destination.parent.mkdir(parents=True,exist_ok=True);cache.mkdir(parents=True,exist_ok=True)
    if destination.exists():
        if digest(destination)!=sha:raise ValueError('immutable adoption evidence collision')
        info=destination.stat()
        identity=f'{sha}-{info.st_mode&0o777:o}-{getattr(info,"st_uid",0)}-{getattr(info,"st_gid",0)}'
    else:
        # Current archive producer creates private files independent of mutable
        # source workspaces. Do not hardlink the mutable source into the archive.
        identity=f'{sha}-600-{os.getuid() if hasattr(os,"getuid") else 0}-{os.getgid() if hasattr(os,"getgid") else 0}'
    canonical=cache/identity
    if not canonical.exists():
        pending=cache/('.pending-'+uuid4().hex)
        try:
            if destination.exists():os.link(destination,pending)
            else:
                with source.open('rb') as incoming,pending.open('xb') as outgoing:
                    import shutil
                    shutil.copyfileobj(incoming,outgoing);outgoing.flush();os.fsync(outgoing.fileno())
                pending.chmod(0o600)
                if digest(pending)!=sha:raise ValueError('adoption source changed during copy')
            try:os.link(pending,canonical)
            except FileExistsError:pass
        finally:pending.unlink(missing_ok=True)
    if canonical.is_symlink() or digest(canonical)!=sha:raise ValueError('immutable evidence cache corrupt')
    original=destination.stat() if destination.exists() else None
    if not original or (original.st_dev,original.st_ino)!=(canonical.stat().st_dev,canonical.stat().st_ino):
        pending=destination.with_name('.evidence-'+uuid4().hex)
        try:os.link(canonical,pending);os.replace(pending,destination)
        finally:pending.unlink(missing_ok=True)
    return dict(sha256=sha,reclaimed_allocated=(getattr(original,'st_blocks',0)*512 if original and original.st_nlink==1 else 0))


def collect_unused(cache,*,now=None,limit=128):
    cache=Path(cache);now=now or time.time();count=0
    if not cache.is_dir():return count
    for path in cache.iterdir():
        if count>=limit:break
        info=path.lstat()
        if path.is_symlink() or not path.is_file():continue
        if info.st_nlink==1 and now-info.st_mtime>7*86400:path.unlink();count+=1
    return count
