from datetime import UTC,datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.message_bus_v2.adapters import StocktwitsMessagesAdapter


@pytest.mark.asyncio
@pytest.mark.parametrize('window,expected',[(None,'2026-11-25'),(datetime(2026,11,20,tzinfo=UTC),'2026-11-20')])
async def test_stocktwits_required_start_preserves_midnight_and_recovery_window(window,expected):
    settings=SimpleNamespace(stocktwits_rapidapi_key='test',stocktwits_rapidapi_base_url='https://example.test',stocktwits_rapidapi_host='example.test')
    adapter=StocktwitsMessagesAdapter(settings,None)
    adapter._json=AsyncMock(return_value={'messages':[]})
    ctx=SimpleNamespace(ticker='COIN',window_start=window,requested_at=datetime(2026,11,26,0,5,tzinfo=UTC),checkpoint={})
    result=await adapter.poll(ctx)
    params=adapter._json.call_args.kwargs['params']
    assert params=={'action':'messages','symbol':'COIN','limit':199,'start':expected}
    assert result.messages==[]
