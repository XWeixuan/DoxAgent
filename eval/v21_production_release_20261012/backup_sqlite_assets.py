"""Include all Event Libraries and monitoring candidate databases in the backup."""
import gzip
import hashlib
import json
import tarfile
from pathlib import Path

DATA=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
BACKUP=DATA/'production-backups/v21-20261012'
archive=BACKUP/'event-configuration-databases.tar.gz'
manifest={}
with (archive.open('wb') as raw,
      gzip.GzipFile(fileobj=raw,mode='wb',compresslevel=1,mtime=0) as compressed,
      tarfile.open(fileobj=compressed,mode='w|') as tar):
    for folder in ('events','runtime/runtime_artifacts','bus/initialization-configurations'):
        for path in (DATA/folder).rglob('*'):
            if not path.is_file() or path.is_symlink() or '.sqlite3' not in path.name:
                continue
            hasher=hashlib.sha256()
            with path.open('rb') as stream:
                while chunk:=stream.read(4*1024*1024):
                    hasher.update(chunk)
            relative=path.relative_to(DATA).as_posix()
            tar.add(path,arcname=relative,recursive=False)
            manifest[relative]=hasher.hexdigest()
        print(folder,len(manifest),flush=True)
with tarfile.open(archive,'r|gz') as tar:
    seen=set()
    for member in tar:
        if member.isfile():
            digest=hashlib.sha256()
            stream=tar.extractfile(member)
            while chunk:=stream.read(4*1024*1024):
                digest.update(chunk)
            assert digest.hexdigest()==manifest[member.name],member.name
            seen.add(member.name)
    assert seen==set(manifest)
(BACKUP/'event-configuration-databases-manifest.json').write_text(json.dumps(manifest,sort_keys=True))
digest=hashlib.sha256()
with archive.open('rb') as stream:
    while chunk:=stream.read(4*1024*1024):
        digest.update(chunk)
receipt=dict(database_count=len(manifest),archive_bytes=archive.stat().st_size,
             sha256=digest.hexdigest(),verified=True)
(BACKUP/'event-configuration-databases-receipt.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt),flush=True)
