"""Private object storage for oversized published Codex documents."""

from __future__ import annotations

from typing import Protocol
from urllib.parse import quote

import httpx


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
    import hashlib
    from pathlib import Path
    from uuid import uuid4

    local = getattr(repository, "local", repository)
    database_path = getattr(local, "path", None)
    if database_path is None:
        return  # Remote-only/in-memory stores have no local canonical file directory.
    if hashlib.sha256(content).hexdigest() != sha256:
        raise ValueError("local published content checksum mismatch")
    cache = Path(database_path).parent / "published-contents" / sha256
    cache.parent.mkdir(parents=True, exist_ok=True)
    if cache.exists():
        if hashlib.sha256(cache.read_bytes()).hexdigest() != sha256:
            raise ValueError("local published content checksum mismatch")
        _queue_w3_asset(cache)
        return
    pending = cache.with_name(sha256 + "." + uuid4().hex + ".tmp")
    pending.write_bytes(content)
    pending.replace(cache)
    _queue_w3_asset(cache)


def _queue_w3_asset(path):
    """Publication notification only; compilation runs outside this transaction."""
    import json
    import logging
    import re
    from pathlib import Path

    from doxagent.settings import DoxAgentSettings

    settings = DoxAgentSettings()
    root = Path(settings.codex_workspace_root).resolve()
    if not path.resolve().is_relative_to(root.parent):
        return
    try:
        text = path.read_text(encoding="utf-8")
        if text.lstrip().startswith("{"):
            value = json.loads(text)
            kind = "d2" if "shells" in value else "policy" if "policies" in value else None
            ticker = value.get("ticker")
        else:
            match = re.match(r"#\s+([A-Z][A-Z0-9.\-]*)\s+Global Research", text)
            kind, ticker = ("d1", match[1]) if match else (None, None)
        if kind and ticker:
            from doxagent.persistent_runtime_v2.w3_research_store import W3ResearchStore
            W3ResearchStore(root).enqueue(kind, ticker, source_path=path, expected_sha=path.name)
    except Exception as exc:
        # A committed publication must never appear failed due to a read index.
        logging.getLogger(__name__).warning("W3 asset queue deferred: %s", type(exc).__name__)
