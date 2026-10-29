from __future__ import annotations

import json
from contextlib import asynccontextmanager
from datetime import datetime, UTC, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from doxagent.message_bus_v2.ctee import parse_category_item, parse_category_response


def test_ctee_observed_category_payload_and_date_precision() -> None:
    row = {
        "articleID": 20260923701847,
        "categoryID": 430501,
        "title": "台積電擴大 OIP 雲端聯盟",
        "content": "半導體供應鏈新聞",
        "publishDatetime": "2026-09-23T18:19:19",
        "hyperLink": "/news/20260923701847-430501",
    }
    payload = parse_category_response("<html><body>" + json.dumps([row]) + "</body></html>")
    message = parse_category_item(payload[0])
    assert message is not None
    assert message.external_id == "20260923701847"
    assert message.publication_time_basis == "EXACT"
    assert message.published_at.hour == 10
    row["publishDatetime"] = "2026-09-23T03:00:00"
    date_only = parse_category_item(row)
    assert date_only is not None and date_only.publication_time_basis == "DATE"
    row["hyperLink"] = "https://evil.example/news/20260923701847-430501"
    assert parse_category_item(row) is None


def test_ctee_native_json_viewer_wrapper():
    assert parse_category_response('<html><body><pre style="white-space:pre-wrap">[]</pre></body></html>') == []


@pytest.mark.asyncio
async def test_ctee_recovery_gate_is_deferral_without_checkpoint_progress():
    from doxagent.message_bus_v2.ctee import CteeSemiconductorAdapter
    from doxagent.site_strategy.client import SiteAccessError
    from doxagent.site_strategy.schema import AccessResult

    @asynccontextmanager
    async def permit():
        yield
    retry = datetime.now(UTC) + timedelta(seconds=60)
    error = SiteAccessError(AccessResult(request_id="request", operation_id="run", site_id="ctee",
        runtime_key="ctee", strategy_revision=1, disposition="RECOVERY_PENDING",
        reason_code="site_recovery_pending", retry_not_before=retry))
    adapter = CteeSemiconductorAdapter(SimpleNamespace(get=AsyncMock(side_effect=error)))
    context = SimpleNamespace(checkpoint={"page": 2}, source=SimpleNamespace(default_parameters={}),
        window_start=None, requested_at=datetime.now(UTC), run_id="run", request_permit=permit)
    result = await adapter.poll_shared(context)
    assert result.site_access_deferred and result.site_access_deferred_reason == "site_recovery_pending"
    assert result.site_access_retry_not_before == retry
    assert result.next_checkpoint == {"page": 2} and result.messages == []


@pytest.mark.asyncio
async def test_ctee_multiple_pages_bind_each_parsed_response_to_its_request():
    from doxagent.message_bus_v2.ctee import CteeSemiconductorAdapter
    from doxagent.message_bus_v2.poll_trace import REQUESTS

    @asynccontextmanager
    async def permit():
        yield
    events = []
    async def get(url, **kwargs):
        number = url.rsplit('/', 1)[-1]
        events.append({'physical_request_id':number,'proof_basis':'TRANSPORT_SEND',
                       'response_received_at':datetime.now(UTC).isoformat(),'status_code':200})
        return 200, url, {}, json.dumps([{}] * 15 if number == '1' else [])
    token = REQUESTS.set(events)
    try:
        result = await CteeSemiconductorAdapter(SimpleNamespace(get=get)).poll_shared(
            SimpleNamespace(checkpoint={},source=SimpleNamespace(default_parameters={}),
                window_start=None,requested_at=datetime.now(UTC),run_id='run',request_permit=permit))
        assert result.acquisition_metadata['validated_request_ids'] == ['1','2']
        assert all(e['list_validated'] for e in events)
    finally:
        REQUESTS.reset(token)
