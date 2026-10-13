"""Archive production business files without duplicating raw SQLite databases."""
import gzip
import hashlib
import json
import tarfile
from pathlib import Path

DATA=Path('/var/lib/docker/volumes/doxagent-v2_v2-data/_data')
BACKUP=DATA/'production-backups/v21-20261012'
folders=['events','runtime/runtime_artifacts','workspaces','research/published-contents',
         'bus/initialization-configurations','bus/adapters']
folders += [str(p.relative_to(DATA)) for p in DATA.glob('*/native-files') if p.parent.name in
            {'research','runtime','bus','read','initialization','scheduler','o4','usage'}]
manifest={}
archive=BACKUP/'business-files.tar.gz'
with (archive.open('wb') as raw,
      gzip.GzipFile(fileobj=raw,mode='wb',compresslevel=1,mtime=0) as compressed,
      tarfile.open(fileobj=compressed,mode='w|') as tar):
    for folder in folders:
        for path in (DATA/folder).rglob('*'):
            if not path.is_file() or path.is_symlink() or '.sqlite3' in path.name or path.name.endswith('.lock'):
                continue
            relative=path.relative_to(DATA).as_posix()
            hasher=hashlib.sha256()
            with path.open('rb') as stream:
                while chunk:=stream.read(4*1024*1024):
                    hasher.update(chunk)
            digest=hasher.hexdigest()
            tar.add(path,arcname=relative,recursive=False)
            manifest[relative]=digest
        print(folder, len(manifest),flush=True)
with tarfile.open(archive,'r|gz') as tar:
    seen=set()
    for member in tar:
        if not member.isfile():
            continue
        stream=tar.extractfile(member)
        digest=hashlib.sha256()
        while chunk:=stream.read(4*1024*1024):
            digest.update(chunk)
        assert digest.hexdigest()==manifest[member.name],member.name
        seen.add(member.name)
    assert seen==set(manifest)
(BACKUP/'business-files-manifest.json').write_text(json.dumps(manifest,sort_keys=True))
digest=hashlib.sha256()
with archive.open('rb') as stream:
    while chunk:=stream.read(4*1024*1024):
        digest.update(chunk)
receipt=dict(file_count=len(manifest),archive_bytes=archive.stat().st_size,sha256=digest.hexdigest(),
             archive='/data/production-backups/v21-20261012/business-files.tar.gz', verified=True)
(BACKUP/'business-files-receipt.json').write_text(json.dumps(receipt,indent=2))
print(json.dumps(receipt),flush=True)
