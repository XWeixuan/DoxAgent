"""One idle, business-owned fetch document per physical identity.

Borrowing removes the page from the pool. Parallel commands use independent
temporary pages; no command waits for an anchor owned by a sibling.
"""

import asyncio
import logging
import time
from dataclasses import dataclass


@dataclass
class FetchAnchor:
    lease: object
    page: object
    gate: object
    response: object
    revision: int
    egress_id: str
    created_at: float
    expiry: asyncio.Task | None = None


class FetchAnchors:
    def __init__(self, *, idle_seconds=90):
        self.idle_seconds = idle_seconds
        self.idle = {}

    async def take(self, identity, egress):
        anchor = self.idle.pop(identity.identity_id, None)
        if anchor is None:
            return None
        anchor.expiry.cancel()
        if (
            anchor.revision != identity.revision
            or anchor.egress_id != egress.egress_id
            or anchor.page.is_closed()
            or anchor.gate.blocked
            or time.monotonic() - anchor.created_at > 300
        ):
            await self._close(anchor)
            return None
        return anchor

    def put(self, identity, anchor):
        if identity.identity_id in self.idle:
            return False
        self.idle[identity.identity_id] = anchor
        anchor.expiry = asyncio.create_task(self._expire(identity.identity_id, anchor))
        return True

    async def _expire(self, key, anchor):
        await asyncio.sleep(self.idle_seconds)
        if self.idle.get(key) is anchor:
            self.idle.pop(key)
            try:
                await self._close(anchor)
            except Exception:
                logging.getLogger(__name__).exception("fetch anchor close failed identity=%s", key)

    @staticmethod
    async def _close(anchor):
        try:
            await anchor.gate.close()
        finally:
            await anchor.lease.__aexit__(None, None, None)

    async def close(self, identity_id=None):
        for key, anchor in tuple(self.idle.items()):
            if identity_id is not None and key != identity_id:
                continue
            self.idle.pop(key)
            anchor.expiry.cancel()
            await asyncio.gather(anchor.expiry, return_exceptions=True)
            await self._close(anchor)
