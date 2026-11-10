"""Isolated real Worker HTTP handlers; no SDK, network, or production writes."""
import asyncio
import hashlib
import json
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace

import httpx

from doxagent.codex_runtime.capabilities import CapabilityTokenCodec
from doxagent.codex_worker.app import create_worker_app
from doxagent.codex_worker.schema import WorkspaceInventory


async def probe():
    entered, release = threading.Event(), threading.Event()
    secret='isolated-worker-probe-key-000000000000'
    with tempfile.TemporaryDirectory(prefix='w3-io-probe-') as directory:
        app=create_worker_app(workspace_root=directory,bearer_token=secret,
                              capability_secret=secret,runtime=SimpleNamespace())
        def inventory(run_id, **kwargs):
            entered.set();release.wait(3)
            return WorkspaceInventory(run_id=run_id,files=[])
        app.state.workspaces.inventory_query=inventory
        cap=CapabilityTokenCodec(secret).issue(run_id='unrelated',operations={'inventory'})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://offline',
                                     headers={'Authorization':'Bearer '+secret}) as client:
            old=asyncio.create_task(client.post('/v1/workspaces/unrelated/inventory-query',json={},
                headers={'X-Workspace-Capability':cap}))
            assert await asyncio.to_thread(entered.wait,2)
            async def measured():
                start=time.perf_counter()
                response=await client.post('/v1/research-packs/'+hashlib.sha256(b'probe').hexdigest(),
                                          json={'files':{'small.md':'small immutable W3 pack'}})
                return response,time.perf_counter()-start
            short=asyncio.create_task(measured())
            try:
                await asyncio.sleep(.2)
                blocked=not short.done()
                release.set()
                response,elapsed=await short
                assert (await old).status_code==200 and response.status_code==200
            finally:release.set()
        return {'unrelated_operation_injected_hold_ms':200,'short_request_blocked':blocked,
                'short_end_to_end_ms':round(elapsed*1000,3),'model_calls':0,'production_writes':0}


if __name__=='__main__':
    print(json.dumps(asyncio.run(probe())))
