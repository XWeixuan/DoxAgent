"""Deterministic cancellation while a permit owner is leaving its context."""
import asyncio
import json
from doxagent.codex_worker.startup_admission import PriorityGate


async def main():
    gate = PriorityGate()
    entered, leave = asyncio.Event(), asyncio.Event()
    async def owner():
        async with gate.acquire():
            entered.set()
            await leave.wait()
    task = asyncio.create_task(owner())
    await entered.wait()
    # A waiter can own the Condition while the departing owner tries to notify.
    await gate.condition.acquire()
    try:
        leave.set()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    finally:
        gate.condition.release()
    blocked = False
    try:
        async with gate.acquire(timeout=.05):
            pass
    except TimeoutError:
        blocked = True
    print(json.dumps(dict(owner_done=task.done(), active=gate.active,
                          waiters=len(gate.waiters), next_request_blocked=blocked)))
    return blocked


if __name__ == '__main__':
    asyncio.run(main())
