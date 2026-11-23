"""Low-frequency owner maintenance, independent of real-time model dispatch."""
import asyncio
from contextlib import closing
from datetime import UTC, datetime
import logging
import time

log=logging.getLogger(__name__)


def collect_inputs(workspaces,jobs,settings):
    from .workspace_retention import collect
    from .snapshot_retention import collect as snapshots
    report={'at':datetime.now(UTC).isoformat()}
    for name,action in (
        ('workspaces',lambda:collect(workspaces,jobs,settings,limit=32)),
        ('initialization_snapshots',lambda:snapshots(workspaces,jobs,settings,limit=16)),
    ):
        try:report[name]=action()
        except Exception as exc:report[name]={'error':type(exc).__name__+':'+str(exc)[:250]}
    jobs.store.metadata('resource_retention',report)
    return report


def quiet(jobs):
    with closing(jobs.store.connect()) as db:
        return not db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone()


async def run(workspaces,jobs,settings):
    while True:
        try:
            # Candidate limits do not bound the global reference proof. Keep
            # collect_inputs as an explicit quiet-maintenance entrypoint until
            # its cross-run proof is scoped; never sweep it beside live W3.
            # SDK tree listing remains an infrequent quiet-maintenance operation.
            # The bounded input owners above never wait for global business idleness.
            from .local_sessions import collect
            result=await collect(jobs)
            if result['deleted'] or result['deferred']:
                await asyncio.to_thread(jobs.store.metadata,'session_retention',result)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('resource owner maintenance failed')
        await asyncio.sleep(300)
