"""Native files actually reachable from one committed backup database."""
import json
import sqlite3
from contextlib import closing
from pathlib import Path

from .content_files import ContentFiles,CHUNK
from .source_lifecycle import native_hashes
from .outbox import quoted


def identities(database, source):
    result=set();source=Path(source)
    def read(files,identity):
        manifest=json.loads((files._path(identity)/'manifest.json').read_text())
        return json.loads(b''.join(files.read(identity,offset,CHUNK) for offset in range(0,manifest['size'],CHUNK)))
    with closing(sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in sorted(tables):
            if table.startswith('sqlite_'):continue
            columns=[r[1] for r in db.execute('PRAGMA table_info('+quoted(table)+')') if r[1] in {'payload','payload_json','inputs','receipt','data_json','packet'}]
            if columns:
                for row in db.execute('SELECT '+','.join(map(quoted,columns))+' FROM '+quoted(table)):
                    for raw in row:
                        if isinstance(raw,str):result.update(native_hashes(raw))
        for table,folder in [('v2_receipt_archive','receipt-files'),('v2_receipt_packs','receipt-packs')]:
            if table not in tables:continue
            for name, in db.execute('SELECT source FROM v2_source_state'):
                files=ContentFiles(source.parent/folder/name)
                for digest, in db.execute('SELECT DISTINCT digest FROM '+table):result.update(native_hashes(read(files,digest)))
        # Explicit holds/unknown external owners are recovery responsibilities,
        # unlike stale refs left by a known source table's older producer.
        if {'artifact','artifact_ref'}<=tables:
            known=sorted(tables|{'transport_pack','transport_archive','transport_exception'})
            result.update(r[0] for r in db.execute("SELECT DISTINCT a.id FROM artifact a JOIN artifact_ref r ON r.artifact_id=a.id WHERE a.codec='json-chunks-v1' AND a.location='native-files/'||substr(a.id,1,2)||'/'||a.id AND r.owner_type NOT IN (SELECT value FROM json_each(?))",(json.dumps(known),)))
        if {'artifact','retention_hold'}<=tables:
            result.update(r[0] for r in db.execute("SELECT a.id FROM artifact a WHERE a.codec='json-chunks-v1' AND a.location='native-files/'||substr(a.id,1,2)||'/'||a.id AND EXISTS(SELECT 1 FROM retention_hold h WHERE h.released_at IS NULL AND (h.scope='ALL' OR h.id=a.id))"))
    files=ContentFiles(source.parent/'native-files');pending=list(result);seen=set()
    while pending:
        identity=pending.pop()
        if identity in seen:continue
        seen.add(identity)
        nested=set(native_hashes(read(files,identity)))-result
        result.update(nested);pending.extend(nested)
    return sorted(result)
