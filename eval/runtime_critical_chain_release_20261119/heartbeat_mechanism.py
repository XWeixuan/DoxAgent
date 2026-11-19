"""Offline quiet-stream/foreign-notification mechanism, without model calls."""
import asyncio,json,time
from types import SimpleNamespace
from unittest.mock import patch
from doxagent.codex_worker.jobs import WorkerJobManager

async def main():
    manager=object.__new__(WorkerJobManager)
    manager._condition=asyncio.Condition();manager._event_revision=0
    manager.EVENT_HEARTBEAT_SECONDS=.1
    notifications=[]
    manager.store=SimpleNamespace(status=lambda _: 'running',events=lambda *args: [])
    async def read(function,*args,**kwargs):return function(*args,**kwargs)
    async def foreign():
        while True:
            await asyncio.sleep(.03)
            notifications.append(time.monotonic())
            async with manager._condition:
                manager._event_revision+=1;manager._condition.notify_all()
    with patch('doxagent.codex_worker.jobs.read_io',read):
        stream=manager.events('quiet')
        first=asyncio.create_task(anext(stream));traffic=asyncio.create_task(foreign())
        try:
            await asyncio.sleep(.65)
            during=first.done()
            traffic.cancel();await asyncio.gather(traffic,return_exceptions=True)
            value=await asyncio.wait_for(first,.2)
            print(json.dumps(dict(heartbeat_during_foreign_traffic=during,configured_interval_ms=100,foreign_observation_ms=650,notifications=len(notifications),max_notification_gap_ms=max((b-a)*1000 for a,b in zip(notifications,notifications[1:])),event=value.event_type)))
        finally:
            traffic.cancel();await asyncio.gather(traffic,return_exceptions=True)
            if not first.done():first.cancel()
            await asyncio.gather(first,return_exceptions=True);await stream.aclose()

asyncio.run(main())
