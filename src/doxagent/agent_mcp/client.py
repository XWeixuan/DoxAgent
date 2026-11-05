"""Fixed GET transport and small bounded continuation state. No database imports."""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from uuid import uuid4

import httpx

MAX_RESULT_BYTES = 128 * 1024


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


class ReadError(Exception):
    def __init__(self, code, message, *, retryable=False, request_id=None):
        super().__init__(message)
        self.code, self.message, self.retryable, self.request_id = (
            code,
            message,
            retryable,
            request_id,
        )

    def wire(self):
        value = {"code": self.code, "message": self.message, "retryable": self.retryable}
        if self.request_id:
            value["request_id"] = self.request_id
        return {"error": value}


class StateStore:
    """Only continuation metadata / pending display rows; TTL and total byte bounds."""

    def __init__(self, max_bytes=4 * 1024 * 1024, max_entries=128, clock=time.time):
        self.max_bytes, self.max_entries, self.clock = max_bytes, max_entries, clock
        self.entries = OrderedDict()
        self.size = 0

    def _remove(self, key):
        _, _, size = self.entries.pop(key)
        self.size -= size

    def put(self, kind, value, expires):
        now = self.clock()
        for key in list(self.entries):
            if self.entries[key][0] <= now:
                self._remove(key)
        size = len(encoded(value))
        if size > self.max_bytes:
            raise ReadError(
                "RESOURCE_TOO_LARGE",
                "The selected display section exceeds the continuation budget.",
            )
        while self.entries and (
            self.size + size > self.max_bytes or len(self.entries) >= self.max_entries
        ):
            self._remove(next(iter(self.entries)))
        key = kind + "_" + uuid4().hex
        self.entries[key] = (min(expires, now + 900), value, size)
        self.size += size
        return key

    def get(self, key, kind):
        if not key.startswith(kind + "_") or key not in self.entries:
            raise ReadError("CURSOR_EXPIRED", "Continuation expired; reopen the query or Case.")
        expires, value, _ = self.entries[key]
        if expires <= self.clock():
            self._remove(key)
            raise ReadError("CURSOR_EXPIRED", "Continuation expired; reopen the query or Case.")
        return value


class ApiClient:
    def __init__(self, base_url, token, *, transport=None):
        self.client = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/api/doxagent/v2/",
            headers={"Authorization": "Bearer " + token},
            timeout=httpx.Timeout(12, connect=3),
            follow_redirects=False,
            transport=transport,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
        )

    async def get(self, path, params=None):
        try:
            response = await self.client.get(
                path.lstrip("/"), params={k: v for k, v in (params or {}).items() if v is not None}
            )
            if response.status_code != 200:
                try:
                    error = response.json().get("error", {})
                except ValueError:
                    error = {}
                raise ReadError(
                    error.get("code", "UPSTREAM_READ_FAILED"),
                    error.get("message", "DoxAgent read failed."),
                    retryable=error.get("retryable", response.status_code >= 500),
                    request_id=error.get("request_id"),
                )
            wire = response.json()
            if response.headers.get("x-doxagent-live-read-at"):
                wire.setdefault("meta", {})["read_at"] = response.headers["x-doxagent-live-read-at"]
            return wire
        except (httpx.HTTPError, ValueError) as exc:
            raise ReadError(
                "READ_UNAVAILABLE", "DoxAgent API is unavailable.", retryable=True
            ) from exc

    async def close(self):
        await self.client.aclose()


def data(wire):
    value = wire["data"]
    if isinstance(value, dict) and "state" in value and "data" in value:
        if value["data"] is None:
            raise ReadError(
                "READ_UNAVAILABLE", value.get("reason") or "This section is unavailable."
            )
        return value["data"]
    return value
