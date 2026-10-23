"""Bounded reads with writer preference for identity maintenance/configuration."""

import asyncio
from contextlib import asynccontextmanager


class OperationGate:
    def __init__(self):
        self.condition = asyncio.Condition()
        self.readers = 0
        self.writer = False
        self.writers_waiting = 0

    @asynccontextmanager
    async def enter(self, *, read: bool, limit: int = 1):
        admitted = False
        async with self.condition:
            if not read:
                self.writers_waiting += 1
            try:
                while (
                    self.writer
                    or (self.writers_waiting if read else self.readers)
                    or (read and self.readers >= limit)
                ):
                    await self.condition.wait()
                if read:
                    self.readers += 1
                else:
                    self.writer = True
                admitted = True
            finally:
                if not read:
                    self.writers_waiting -= 1
                self.condition.notify_all()
        try:
            yield
        finally:
            if admitted:
                async with self.condition:
                    if read:
                        self.readers -= 1
                    else:
                        self.writer = False
                    self.condition.notify_all()
