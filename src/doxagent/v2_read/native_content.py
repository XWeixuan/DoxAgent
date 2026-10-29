"""Versioned native JSON codec. Scalar query columns remain inline.

Files are published before a reference can enter a business transaction. Native
content is business evidence and is never eligible for diagnostic age deletion.
"""
import json
import sqlite3
import copy
import threading
import sys
from collections import OrderedDict
from pathlib import Path
from .content_files import ContentFiles, ContentUnavailable

MARKER = "$doxagent_content_v1"
_CACHE = OrderedDict()
_CACHE_BYTES = 0
_CACHE_LOCK = threading.Lock()


def decoded_size(value):
    """Account the retained Python graph once, without serializing another copy."""
    seen=set()
    def size(item):
        if id(item) in seen:return 0
        seen.add(id(item))
        return sys.getsizeof(item)+(sum(size(k)+size(v) for k,v in item.items())
            if isinstance(item,dict) else sum(map(size,item)) if isinstance(item,list) else 0)
    return size(value)


class NativeContent:
    def __init__(self, database, *, cache_bytes=0):
        self.database = Path(database).resolve()
        self.definition_connection = None
        self.files = ContentFiles(Path(database).resolve().parent / "native-files")
        self.cache_bytes = cache_bytes
        self._definitions = OrderedDict()

    def encode(self, value, *, keep=()):
        def pack(item, root=False, path=""):
            root = root or path in keep
            if not root:
                raw = json.dumps(item, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
                if len(raw) >= 16384:
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

    def decode(self, value, *, expand_definitions=True, shared=False):
        from .query_budget import check_deadline
        check_deadline()
        if isinstance(value, dict):
            from .serving_schema import DEFINITION_MARKER
            if DEFINITION_MARKER in value:
                if not expand_definitions:
                    return {k:self.decode(v,expand_definitions=False) for k,v in value.items()}
                identity = value[DEFINITION_MARKER]
                if identity in self._definitions:
                    self._definitions.move_to_end(identity)
                    result = self._definitions[identity]
                    return result if shared else copy.deepcopy(result)
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
                    result = self.decode(result,expand_definitions=expand_definitions,shared=shared)
                    if len(row[0]) <= 65536:
                        self._definitions[identity] = copy.deepcopy(result)
                        while len(self._definitions)>32:
                            self._definitions.popitem(last=False)
                    return result
                finally:
                    if owned:db.close()
            if MARKER in value:
                if set(value) != {MARKER, "bytes"} or not isinstance(value["bytes"], int) or value["bytes"] < 0:
                    raise ContentUnavailable()
                global _CACHE_BYTES
                identity = value[MARKER]
                key = (str(self.files.root), identity, value['bytes'])
                with _CACHE_LOCK:
                    cached = _CACHE.get(key) if self.cache_bytes else None
                    if cached is not None:
                        _CACHE.move_to_end(key)
                if cached is not None:
                    return cached[1] if shared else copy.deepcopy(cached[1])
                raw = self.files.read_all(identity)
                if len(raw) != value["bytes"]:
                    raise ContentUnavailable()
                check_deadline()
                try:
                    result = json.loads(raw)
                except ValueError as exc:
                    raise ContentUnavailable() from exc
                if self.cache_bytes and len(raw) <= self.cache_bytes:
                    size=decoded_size(result)
                    with _CACHE_LOCK:
                        prior = _CACHE.pop(key, None)
                        _CACHE_BYTES += size - (prior[0] if prior else 0)
                        _CACHE[key] = (size, result)
                        while _CACHE_BYTES > self.cache_bytes or len(_CACHE) > 64:
                            _, item = _CACHE.popitem(last=False)
                            _CACHE_BYTES -= item[0]
                return copy.deepcopy(result) if self.cache_bytes and not shared else result
            return {key: self.decode(child,expand_definitions=expand_definitions,shared=shared) for key, child in value.items()}
        if isinstance(value, list):
            return [self.decode(child,expand_definitions=expand_definitions,shared=shared) for child in value]
        return value

    def text(self, raw):
        return json.dumps(self.decode(json.loads(raw)), ensure_ascii=False, separators=(",", ":"), sort_keys=True) if MARKER in raw or '$doxagent_definition_v4' in raw else raw

    def row(self, cursor, values):
        # Only selected JSON columns are loaded. Status-only SQL never opens files.
        values = tuple(self.text(value) if column[0] in {"payload_json", "payload", "inputs", "receipt", "data_json"}
                       and isinstance(value, str) and (MARKER in value or '$doxagent_definition_v4' in value) else value
                       for column, value in zip(cursor.description, values))
        return sqlite3.Row(cursor, values)

    @staticmethod
    def raw_row(cursor, values):
        """Thin/status consumers explicitly retain native references without expansion."""
        return sqlite3.Row(cursor, values)

    def source_row(self, row):
        return {key: self.text(value) if isinstance(value, str) and MARKER in value
                and key in {"payload_json", "payload", "inputs", "receipt", "data_json"} else value for key, value in row.items()}
