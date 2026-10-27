"""Bounded best-effort technical events, outside website/identity execution slots."""

import asyncio
import logging
from .storage import registry_io


class DiagnosticEvents:
    def __init__(self, repository):
        self.repository = repository
        self.queue = asyncio.Queue(maxsize=2048)
        self.task = None
        self.dropped = 0
        self.stopping = False

    def submit(self, event):
        try:
            self.queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped += 1
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self.run())

    async def run(self):
        while not self.stopping or not self.queue.empty():
            try:
                first = await asyncio.wait_for(self.queue.get(), 0.5)
            except TimeoutError:
                continue
            batch = [first]
            while len(batch) < 64 and not self.queue.empty():
                batch.append(self.queue.get_nowait())
            try:
                await registry_io(self.repository.append_events, batch)
            except Exception:
                self.dropped += len(batch)
                logging.getLogger(__name__).exception(
                    "Site diagnostic batch not persisted; evidence remains unproven"
                )

    async def close(self):
        self.stopping = True
        if self.task:
            await self.task
