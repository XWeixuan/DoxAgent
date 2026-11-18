"""Private object storage for oversized published Codex documents."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Protocol
from urllib.parse import quote
from uuid import uuid4

import httpx

LOCAL_BODY_CODEC = 'gzip-v1'


def local_body_refs_enabled():
    """Enable new writes after all readers and recovery tools have been upgraded."""
    return os.getenv('DOXAGENT_PUBLISHED_CONTENT_REFS') == '1'


def local_body_path(directory: Path, digest: str, *, compressed: bool) -> Path:
    if not re.fullmatch(r'[0-9a-f]{64}', digest):
        raise ValueError('invalid published content identity')
    return directory / 'published-contents' / (digest + ('.gz' if compressed else ''))


def read_local_body(path: Path, digest: str) -> bytes:
    data = path.read_bytes()
    if path.suffix == '.gz':
        data = gzip.decompress(data)
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('PUBLISHED_CHECKSUM_MISMATCH')
    return data


def load_published_body(directory: Path, record: dict) -> bytes:
    digest = record['sha256']
    codec = record.get('local_content_codec')
    if codec is not None and codec != LOCAL_BODY_CODEC:
        raise ValueError('unsupported published content codec')
    text = record.get('content_text')
    if text is not None:
        data = text.encode('utf-8')
    else:
        compressed = local_body_path(directory, digest, compressed=True)
        legacy = local_body_path(directory, digest, compressed=False)
        path = compressed if codec is not None or compressed.is_file() else legacy
        if not path.is_file():
            raise ValueError('REMOTE_CONTENT_BACKFILL_REQUIRED')
        data = read_local_body(path, digest)
    if len(data) != record['size_bytes'] or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('PUBLISHED_CHECKSUM_MISMATCH')
    return data


def put_local_body(directory: Path, digest: str, data: bytes, *, compressed: bool) -> Path:
    if hashlib.sha256(data).hexdigest() != digest:
        raise ValueError('PUBLISHED_CHECKSUM_MISMATCH')
    path = local_body_path(directory, digest, compressed=compressed)
    if path.is_file():
        read_local_body(path, digest)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(f'.{path.name}.{uuid4().hex}.tmp')
    try:
        with pending.open('xb') as stream:
            stream.write(gzip.compress(data, mtime=0) if compressed else data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(pending, path)
        if os.name != 'nt':
            fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        return path
    finally:
        pending.unlink(missing_ok=True)


def compact_inline_documents(database: Path, *, after: str = '', limit: int = 32):
    """Convert one page of legacy local publications after readers are upgraded."""
    import sqlite3
    from contextlib import closing

    if not local_body_refs_enabled() or not 1 <= limit <= 128:
        raise ValueError('published body conversion requires enabled refs and a bounded page')
    database = Path(database).resolve(strict=True)
    with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as db:
        page = db.execute(
            "SELECT record_key FROM codex_runtime_records WHERE record_type='published_documents' "
            "AND record_key>? ORDER BY record_key LIMIT ?", (after, limit),
        ).fetchall()
    converted = 0
    conflicts = []
    for key, in page:
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro', uri=True)) as db:
            row = db.execute(
                'SELECT p.payload_json,a.payload_json FROM codex_runtime_records p '
                "JOIN codex_runtime_records a ON a.record_type='artifacts' AND a.record_key=p.record_key "
                "AND a.run_id=p.run_id WHERE p.record_type='published_documents' AND p.record_key=?", (key,),
            ).fetchone()
        if row is None:
            continue
        original, proof = row
        record, artifact = json.loads(original), json.loads(proof)
        if record.get('content_text') is None:
            continue
        if not artifact.get('published') or any(record[k] != artifact[k] for k in ('sha256', 'size_bytes')):
            raise ValueError(f'publication has no matching immutable artifact: {key}')
        raw = load_published_body(database.parent, record)
        put_local_body(database.parent, record['sha256'], raw, compressed=True)
        del record['content_text']
        record['local_content_codec'] = LOCAL_BODY_CODEC
        with closing(sqlite3.connect(database, timeout=.1)) as db, db:
            changed = db.execute(
                "UPDATE codex_runtime_records SET payload_json=? WHERE record_type='published_documents' "
                "AND record_key=? AND payload_json=? AND EXISTS (SELECT 1 FROM codex_runtime_records "
                "WHERE record_type='artifacts' AND record_key=? AND payload_json=?)",
                (json.dumps(record, ensure_ascii=False, separators=(',', ':')), key, original, key, proof),
            ).rowcount
        converted += changed
        if not changed:
            conflicts.append(key)
    return {'scanned': len(page), 'converted': converted, 'cursor': page[-1][0] if page else after,
            'complete': len(page) < limit, 'conflicts': conflicts}


class PublishedDocumentStorage(Protocol):
    async def put(self, path: str, content: bytes, content_type: str) -> None: ...

    async def get(self, path: str) -> bytes: ...


class SupabasePublishedDocumentStorage:
    """Server-only client for a private Supabase Storage bucket."""

    def __init__(self, project_url: str, secret_key: str, bucket: str) -> None:
        self._base_url = project_url.rstrip("/")
        self._secret_key = secret_key
        self._bucket = bucket

    def _object_url(self, path: str) -> str:
        bucket = quote(self._bucket, safe="")
        object_path = quote(path.lstrip("/"), safe="/")
        return f"{self._base_url}/storage/v1/object/{bucket}/{object_path}"

    def _headers(self) -> dict[str, str]:
        return {
            "apikey": self._secret_key,
            "Authorization": f"Bearer {self._secret_key}",
        }

    async def put(self, path: str, content: bytes, content_type: str) -> None:
        headers = {**self._headers(), "Content-Type": content_type, "x-upsert": "true"}
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(self._object_url(path), headers=headers, content=content)
        response.raise_for_status()

    async def get(self, path: str) -> bytes:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.get(self._object_url(path), headers=self._headers())
        response.raise_for_status()
        return response.content


def cache_local_published_content(repository, sha256: str, content: bytes) -> None:
    """Mirror external published bodies for checksum-verified offline read projection."""
    local = getattr(repository, "local", repository)
    database_path = getattr(local, "path", None)
    if database_path is None:
        return  # Remote-only/in-memory stores have no local canonical file directory.
    cache = put_local_body(
        Path(database_path).parent, sha256, content, compressed=local_body_refs_enabled(),
    )
    _queue_w3_asset(cache)


def _queue_w3_asset(path):
    """Publication notification only; compilation runs outside this transaction."""
    import json
    import logging

    from doxagent.settings import DoxAgentSettings

    settings = DoxAgentSettings()
    root = Path(settings.codex_workspace_root).resolve()
    if not path.resolve().is_relative_to(root.parent):
        return
    try:
        text = read_local_body(path, path.stem).decode('utf-8')
        if text.lstrip().startswith("{"):
            value = json.loads(text)
            kind = "d2" if "shells" in value else "policy" if "policies" in value else None
            ticker = value.get("ticker")
        else:
            match = re.match(r"#\s+([A-Z][A-Z0-9.\-]*)\s+Global Research", text)
            kind, ticker = ("d1", match[1]) if match else (None, None)
        if kind and ticker:
            from doxagent.persistent_runtime_v2.w3_research_store import W3ResearchStore
            W3ResearchStore(root).enqueue(kind, ticker, source_path=path, expected_sha=path.stem)
    except Exception as exc:
        # A committed publication must never appear failed due to a read index.
        logging.getLogger(__name__).warning("W3 asset queue deferred: %s", type(exc).__name__)
