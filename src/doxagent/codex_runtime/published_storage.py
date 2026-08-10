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
