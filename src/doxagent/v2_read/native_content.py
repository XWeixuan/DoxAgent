"""Versioned native JSON codec. Scalar query columns remain inline.

Files are published before a reference can enter a business transaction. Native
content is business evidence and is never eligible for diagnostic age deletion.
"""
import json
import sqlite3
from pathlib import Path
from .content_files import ContentFiles, ContentUnavailable, CHUNK

MARKER = "$doxagent_content_v1"


class NativeContent:
    def __init__(self, database):
        self.database = Path(database).resolve()
        self.definition_connection = None
        self.files = ContentFiles(Path(database).resolve().parent / "native-files")

    def encode(self, value, *, keep=()):
        def pack(item, root=False, path=""):
            root = root or path in keep
            raw = json.dumps(item, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
            if not root and len(raw) >= 16384:
                return {MARKER: self.files.put(raw), "bytes": len(raw)}
            if isinstance(item, dict):
                if MARKER in item:
                    raise ValueError("reserved native content marker")
                return {key: pack(child, root=root and key in keep,
                                  path=f'{path}.{key}' if path else key) for key, child in item.items()}
            if isinstance(item, list):
                return [pack(child, path=path+'[]') for child in item]
            return item
        return json.dumps(pack(value, root=isinstance(value, dict)), ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    def decode(self, value, *, expand_definitions=True):
        from .query_budget import check_deadline
        check_deadline()
        if isinstance(value, dict):
            from .serving_schema import DEFINITION_MARKER
            if DEFINITION_MARKER in value:
                if not expand_definitions:
                    return {k:self.decode(v,expand_definitions=False) for k,v in value.items()}
                identity = value[DEFINITION_MARKER]
                db = self.definition_connection
                owned = db is None
                if owned:
                    db = sqlite3.connect(self.database.as_uri()+'?mode=ro',uri=True)
                try:
                    row = db.execute('SELECT payload AS definition_json FROM definitions WHERE sha256=?',(identity,)).fetchone()
                    if row is None:
                        raise ContentUnavailable()
                    result = json.loads(row[0])
                    import hashlib
                    if hashlib.sha256(row[0].encode()).hexdigest() != identity:
                        raise ContentUnavailable()
                    return self.decode(result,expand_definitions=expand_definitions)
                finally:
                    if owned:db.close()
            if MARKER in value:
                if set(value) != {MARKER, "bytes"} or not isinstance(value["bytes"], int) or value["bytes"] < 0:
                    raise ContentUnavailable()
                raw = b"".join(self.files.read(value[MARKER], offset, CHUNK) for offset in range(0, value["bytes"], CHUNK))
                if len(raw) != value["bytes"]:
                    raise ContentUnavailable()
                import hashlib
                if hashlib.sha256(raw).hexdigest() != value[MARKER]:
                    raise ContentUnavailable()
                check_deadline()
                try:
                    return json.loads(raw)
                except ValueError as exc:
                    raise ContentUnavailable() from exc
            return {key: self.decode(child,expand_definitions=expand_definitions) for key, child in value.items()}
        if isinstance(value, list):
            return [self.decode(child,expand_definitions=expand_definitions) for child in value]
        return value

    def text(self, raw):
        return json.dumps(self.decode(json.loads(raw)), ensure_ascii=False, separators=(",", ":"), sort_keys=True) if MARKER in raw or '$doxagent_definition_v4' in raw else raw

    def row(self, cursor, values):
        # Only selected JSON columns are loaded. Status-only SQL never opens files.
        values = tuple(self.text(value) if column[0] in {"payload_json", "payload", "inputs", "receipt", "data_json"}
                       and isinstance(value, str) and (MARKER in value or '$doxagent_definition_v4' in value) else value
                       for column, value in zip(cursor.description, values))
        return sqlite3.Row(cursor, values)

    def source_row(self, row):
        return {key: self.text(value) if isinstance(value, str) and MARKER in value
                and key in {"payload_json", "payload", "inputs", "receipt", "data_json"} else value for key, value in row.items()}
