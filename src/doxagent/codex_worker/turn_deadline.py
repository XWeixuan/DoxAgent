"""One SDK execution clock; result persistence/transport are separate phases."""

import asyncio


class SdkExecutionTimeout(TimeoutError):
    code = "CODEX_TURN_TIMEOUT"


async def run_with_deadline(handle, deadline):
    # Keep SDK start/run/cancellation in its original task: no wait_for child
    # task may take ownership of an SDK/AnyIO cancellation scope.
    timer = asyncio.timeout_at(deadline)
    try:
        async with timer:
            return await handle.run()
    except TimeoutError:
        if not timer.expired():
            raise
        completed = getattr(handle, "sdk_completed_monotonic", None)
        result = getattr(handle, "sdk_completed_result", None)
        if completed is not None and completed <= deadline and result is not None:
            return result  # Only accounting/probe/receipt work was interrupted.
        try:
            async with asyncio.timeout(5):
                await handle.interrupt()
        except Exception:
            pass  # Parent still owns physical cleanup/quarantine.
        raise SdkExecutionTimeout("SDK execution deadline exceeded") from None
