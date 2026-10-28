"""A small pool of idle business fetch documents per physical identity.

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
    lifecycle_task: asyncio.Task | None = None
    frozen: bool = False
    thawed: bool = False
    closed: bool = False


class FetchAnchors:
    def __init__(self, *, idle_seconds=30):
        self.idle_seconds = idle_seconds
        self.idle = {}

    async def take(self, identity, egress):
        bucket = self.idle.get(identity.identity_id, [])
        if not bucket:
            return None
        anchor = bucket.pop()
        if not bucket:
            self.idle.pop(identity.identity_id, None)
        try:
            anchor.expiry.cancel()
            if anchor.lifecycle_task:
                await anchor.lifecycle_task
            if anchor.closed:return None
            if (
                anchor.revision != identity.revision
                or anchor.egress_id != egress.egress_id
                or anchor.page.is_closed()
                or anchor.gate.blocked
                or time.monotonic() - anchor.created_at > 300
            ):
                await self._close(anchor)
                return None
            if anchor.frozen:
                if not await self._lifecycle(anchor, 'active'):
                    await self._close(anchor)
                    return None
                anchor.thawed = True
            return anchor
        except BaseException:
            await asyncio.shield(self._close(anchor))
            raise

    @staticmethod
    async def _lifecycle(anchor, state):
        # Verified business pages only. Freezing an idle background document stops
        # ad/script CPU; cookies, profile, page URL and network identity stay intact.
        context = getattr(anchor.page, 'context', None)
        if context is None or not hasattr(context, 'new_cdp_session'):
            return False
        session = None
        try:
            async with asyncio.timeout(1):
                session = await context.new_cdp_session(anchor.page)
                await session.send('Page.setWebLifecycleState', {'state':state})
                anchor.frozen = state == 'frozen'
                return True
        except Exception:
            return False
        finally:
            if session is not None:
                try:
                    async with asyncio.timeout(1):
                        await session.detach()
                except Exception:
                    pass

    def put(self, identity, anchor, *, freeze=True, limit=None):
        bucket = self.idle.setdefault(identity.identity_id, [])
        limit = limit or 1
        if len(bucket) >= limit:
            return False
        bucket.append(anchor)
        if freeze:
            async def freeze_or_close():
                if not await self._lifecycle(anchor,'frozen'):
                    if anchor in bucket:bucket.remove(anchor)
                    if not bucket and self.idle.get(identity.identity_id) is bucket:self.idle.pop(identity.identity_id,None)
                    anchor.lifecycle_task=None
                    if anchor.expiry:anchor.expiry.cancel()
                    await self._close(anchor)
            anchor.lifecycle_task = asyncio.create_task(freeze_or_close())
        anchor.expiry = asyncio.create_task(self._expire(identity.identity_id, anchor))
        return True

    async def _expire(self, key, anchor):
        await asyncio.sleep(self.idle_seconds)
        bucket = self.idle.get(key, [])
        if anchor in bucket:
            bucket.remove(anchor)
            if not bucket:
                self.idle.pop(key, None)
            try:
                await self._close(anchor)
            except Exception:
                logging.getLogger(__name__).exception("fetch anchor close failed identity=%s", key)

    @staticmethod
    async def _close(anchor):
        if anchor.closed:return
        anchor.closed=True
        if anchor.lifecycle_task and not anchor.lifecycle_task.done():
            anchor.lifecycle_task.cancel()
            await asyncio.gather(anchor.lifecycle_task, return_exceptions=True)
        try:
            await anchor.gate.close()
        finally:
            await anchor.lease.__aexit__(None, None, None)

    async def close(self, identity_id=None):
        for key, bucket in tuple(self.idle.items()):
            if identity_id is not None and key != identity_id:
                continue
            self.idle.pop(key)
            for anchor in bucket:
                anchor.expiry.cancel()
                await asyncio.gather(anchor.expiry, return_exceptions=True)
                await self._close(anchor)
