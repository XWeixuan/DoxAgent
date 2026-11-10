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
        ('workspaces',lambda:collect(workspaces,jobs,settings,limit=4)),
        ('initialization_snapshots',lambda:snapshots(workspaces,jobs,settings,limit=8)),
    ):
        try:report[name]=action()
        except Exception as exc:report[name]={'error':type(exc).__name__+':'+str(exc)[:250]}
    jobs.store.metadata('resource_retention',report)
    return report


def quiet(jobs):
    with closing(jobs.store.connect()) as db:
        return not db.execute("SELECT 1 FROM jobs WHERE status IN ('queued','running') LIMIT 1").fetchone()


async def run(workspaces,jobs,settings):
    session_next=time.monotonic()+6*3600
    while True:
        await asyncio.sleep(900)
        try:
            await asyncio.to_thread(collect_inputs,workspaces,jobs,settings)
            # SDK tree listing remains an infrequent quiet-maintenance operation.
            # The bounded input owners above never wait for global business idleness.
            if time.monotonic()>=session_next and jobs.session_retention is not None:
                if await asyncio.to_thread(quiet,jobs):
                    result=await jobs.session_retention.collect(limit=16)
                    await asyncio.to_thread(jobs.store.metadata,'session_retention',result)
                    session_next=time.monotonic()+6*3600
                else:
                    await asyncio.to_thread(jobs.store.metadata,'session_retention_deferred',
                        {'at':datetime.now(UTC).isoformat(),'reason':'active jobs; quiet SDK tree maintenance'})
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception('resource owner maintenance failed')
