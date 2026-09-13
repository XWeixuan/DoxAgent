from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx
import pytest

from doxagent.message_bus_v2.adapters import AdapterRegistry
from doxagent.message_bus_v2.schema import PollContext, UpdateActor
from doxagent.settings import DoxAgentSettings
from tests.test_message_bus_v2 import _bus


@pytest.mark.asyncio
async def test_benzinga_window_cursor_and_cutoff(tmp_path):
    requests = []
    cutoff = datetime(2026, 9, 13, 6, tzinfo=UTC)
    def handler(request):
        requests.append(request)
        page = int(request.url.params['page'])
        return httpx.Response(200, json=[{
            'id': str(i), 'title': 'MU test', 'body': 'MU provider body',
            'created': '2026-09-11T12:00:00Z',
            'updated': '2026-09-12T12:00:00Z' if i < 100 else '2026-09-13T07:00:00Z',
            'url': 'https://example.com/' + str(i), 'author': 'Benzinga',
        } for i in (range(100) if page == 0 else [100])])
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    registry = AdapterRegistry(DoxAgentSettings(benzinga_api_key='fixture'), adapter_root=tmp_path, client=client)
    repo, bus = _bus(tmp_path / 'bus.db')
    source = bus.require_source('benzinga_news')
    binding = bus.configure_binding(ticker='MU', source_id=source.source_id, actor=UpdateActor.SYSTEM)
    @asynccontextmanager
    async def permit():
        yield
    context = PollContext(ticker='MU', source=source, binding=binding, requested_at=cutoff,
        window_start=datetime(2026, 9, 12, 6, tzinfo=UTC), window_cutoff=cutoff, request_permit=permit)
    adapter = registry.resolve(source.adapter_ref, source_version=source.version)
    first = await adapter.poll(context)
    assert not first.window_done and len(first.messages) == 100
    second = await adapter.poll(context.model_copy(update={'checkpoint': first.next_checkpoint}))
    assert second.window_done and not second.messages
    assert first.window_coverage == second.window_coverage == 'UNKNOWN'
    assert requests[0].url.params['updatedSince'] == str(int(context.window_start.timestamp()))
    assert requests[1].url.params['page'] == '1'
    await client.aclose()
