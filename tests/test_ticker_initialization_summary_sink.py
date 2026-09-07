import json

import httpx
import pytest

from doxagent.ticker_initialization.sync import SupabaseSummarySink


@pytest.mark.asyncio
async def test_rpc_summary_allowlist_excludes_bodies_and_credentials():
    requests = []

    async def handle(request):
        requests.append(json.loads(request.content))
        return httpx.Response(204)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    sink = SupabaseSummarySink("https://offline.invalid", "fake-server-secret", client=client)
    summary = {
        "initialization_id": "test",
        "ticker": "MU",
        "state_seq": 1,
        "status": "RUNNING",
        "phase": "D1",
        "created_at": "2026-09-05T00:00:00Z",
        "updated_at": "2026-09-05T00:00:00Z",
        "manual_resume_required": False,
        "has_error": False,
        "inputs": "large private body",
        "error": "sensitive error",
        "token": "private token",
    }
    await sink.upsert(summary)
    await sink.close()
    assert set(requests[0]["summary"]) == set(summary) - {"inputs", "error", "token"}
    assert len(json.dumps(requests[0])) < 1024
