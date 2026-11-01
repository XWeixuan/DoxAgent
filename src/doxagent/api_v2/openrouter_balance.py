"""Account credits: one shared, short network read per five minutes; no SQL."""
import asyncio
import os
import time
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import httpx


class OpenRouterBalanceMonitor:
    def __init__(self, *, key_file=None, transport=None, clock=time.monotonic):
        self.key_file = Path(key_file or os.getenv(
            'OPENROUTER_MANAGEMENT_KEY_FILE', '/run/secrets/openrouter_management_key'))
        self.transport, self.clock = transport, clock
        self.value = {'status':'NOT_CONFIGURED','balance_usd':None,'checked_at':None}
        self.next_read = 0
        self.task = None

    async def status(self):
        if self.clock() >= self.next_read:
            if self.task is None:
                self.task = asyncio.create_task(self._read())
            task = self.task
            try:
                await asyncio.shield(task)
            finally:
                if task.done() and self.task is task:
                    self.task = None
        return dict(self.value)

    async def _read(self):
        try:
            key = self.key_file.read_text(encoding='utf-8').strip()
            if not key:
                raise FileNotFoundError
            async with httpx.AsyncClient(timeout=httpx.Timeout(3,connect=2),transport=self.transport) as client:
                response = await client.get('https://openrouter.ai/api/v1/credits',
                                            headers={'Authorization':'Bearer '+key})
                response.raise_for_status()
                data = response.json()['data']
                balance = Decimal(str(data['total_credits'])) - Decimal(str(data['total_usage']))
                if not balance.is_finite():
                    raise ValueError('invalid credit amount')
            self.value = {'status':'AVAILABLE','balance_usd':format(balance,'f'),
                          'checked_at':datetime.now(UTC).isoformat().replace('+00:00','Z')}
        except FileNotFoundError:
            self.value = {'status':'NOT_CONFIGURED','balance_usd':None,'checked_at':None}
        except Exception:
            # Never return/log provider responses, credential paths or headers.
            self.value = {**self.value,'status':'STALE' if self.value['balance_usd'] is not None else 'UNAVAILABLE'}
        finally:
            self.next_read = self.clock() + 300
