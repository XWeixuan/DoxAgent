from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from email.utils import format_datetime

import httpx
import pytest

from doxagent.message_bus_v2.news_adapters import YahooFinanceNewsAdapter
from doxagent.message_bus_v2.yahoo_transport import YahooRateLimited, YahooTransport
from doxagent.settings import DoxAgentSettings
from tests.test_message_bus_v2_news_sources import _context


def make_transport(handler, **kwargs):
    calls = []
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def factory(**options):
        calls.append(options)
        return client

    return YahooTransport(session_factory=factory, gap=0, jitter=0, **kwargs), calls


async def test_429_global_circuit_retry_after_and_single_half_open():
    now = [0.0]
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "600"})

    transport, factories = make_transport(handler, clock=lambda: now[0])
    try:
        for expected in (600, 900, 1800, 1800):
            with pytest.raises(YahooRateLimited) as error:
                await transport.request("GET", "https://finance.yahoo.com/test")
            assert error.value.retry_after_seconds == expected
            count = len(requests)
            results = await asyncio.gather(
                *[transport.request("GET", "https://finance.yahoo.com/other") for _ in range(3)],
                return_exceptions=True,
            )
            assert all(isinstance(r, YahooRateLimited) for r in results)
            assert len(requests) == count
            now[0] += expected
        assert factories == [{"impersonate": "chrome", "max_clients": 1}]
    finally:
        transport.close()


async def test_retry_after_http_date():
    date = format_datetime(datetime.fromtimestamp(2200, UTC), usegmt=True)
    transport, _ = make_transport(
        lambda _: httpx.Response(429, headers={"Retry-After": date}),
        clock=lambda: 0,
        wall_clock=lambda: 1000,
    )
    try:
        with pytest.raises(YahooRateLimited) as error:
            await transport.request("GET", "https://finance.yahoo.com/test")
        assert error.value.retry_after_seconds == 1200
    finally:
        transport.close()


@pytest.mark.parametrize("status", [429, 403, 401, 503])
async def test_ncp_block_or_service_failure_never_falls_back(tmp_path, status):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status)

    transport, _ = make_transport(handler)
    try:
        context, _ = _context(tmp_path, "yahoo_finance_news", {})
        async with httpx.AsyncClient() as client:
            adapter = YahooFinanceNewsAdapter(
                DoxAgentSettings(_env_file=None), client, transport=transport
            )
            with pytest.raises((YahooRateLimited, httpx.HTTPStatusError)):
                await adapter.poll(context)
        assert len(requests) == 1
        assert requests[0].url.path == "/xhr/ncp"
    finally:
        transport.close()


@pytest.mark.parametrize(
    "bootstrap,recovery,count", [(False, False, 20), (True, False, 100), (False, True, 100)]
)
async def test_ncp_poll_size_and_empty_success(tmp_path, bootstrap, recovery, count):
    requests = []

    def handler(request):
        requests.append(request)
        assert json.loads(request.content)["serviceConfig"]["snippetCount"] == count
        return httpx.Response(200, json={"data": []})

    transport, _ = make_transport(handler)
    try:
        context, _ = _context(tmp_path, "yahoo_finance_news", {"snippet_count": 200})
        context = context.model_copy(
            update={"is_bootstrap": bootstrap, "is_gap_recovery": recovery}
        )
        async with httpx.AsyncClient() as client:
            result = await YahooFinanceNewsAdapter(
                DoxAgentSettings(_env_file=None), client, transport=transport
            ).poll(context)
        assert result.acquisition_metadata["requested_count"] == count
        assert len(requests) == 1
    finally:
        transport.close()


async def test_query_429_does_not_continue_to_query2_or_reader(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(404 if request.url.path == "/xhr/ncp" else 429)

    transport, _ = make_transport(handler)
    try:
        context, _ = _context(tmp_path, "yahoo_finance_news", {})
        async with httpx.AsyncClient() as client:
            with pytest.raises(YahooRateLimited):
                await YahooFinanceNewsAdapter(
                    DoxAgentSettings(_env_file=None), client, transport=transport
                ).poll(context)
        assert [r.url.host for r in requests] == ["finance.yahoo.com", "query1.finance.yahoo.com"]
    finally:
        transport.close()


def test_session_reused_across_short_lived_caller_loops():
    calls = []

    def handler(request):
        if calls:
            assert request.headers["Cookie"] == "yahoo_session=shared"
        calls.append(request)
        return httpx.Response(
            200, json={"data": []}, headers={"Set-Cookie": "yahoo_session=shared; Path=/"}
        )

    transport, factories = make_transport(handler)
    try:
        for _ in range(2):
            asyncio.run(transport.request("GET", "https://finance.yahoo.com/test"))
        assert len(factories) == 1
    finally:
        transport.close()


async def test_global_serialization_and_stagger():
    now, starts, active, peak = [0.0], [], [0], [0]

    async def sleep(seconds):
        now[0] += seconds
        await asyncio.sleep(0)

    async def handler(request):
        starts.append(now[0])
        active[0] += 1
        peak[0] = max(peak[0], active[0])
        await asyncio.sleep(0)
        active[0] -= 1
        return httpx.Response(200, json={"data": []})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    transport = YahooTransport(
        session_factory=lambda **_: client, gap=2, jitter=0, clock=lambda: now[0], sleep=sleep
    )
    try:
        await asyncio.gather(
            *[
                transport.request("GET", f"https://finance.yahoo.com/{ticker}")
                for ticker in ("MU", "NVDA", "AMD")
            ]
        )
        assert starts == [0, 2, 4]
        assert peak[0] == 1
    finally:
        transport.close()


async def test_success_resets_backoff():
    now, statuses = [0.0], iter([429, 200, 429])
    transport, _ = make_transport(
        lambda _: httpx.Response(next(statuses), json={"data": []}), clock=lambda: now[0]
    )
    try:
        with pytest.raises(YahooRateLimited):
            await transport.request("GET", "https://finance.yahoo.com/test")
        now[0] = 300
        await transport.request("GET", "https://finance.yahoo.com/test")
        with pytest.raises(YahooRateLimited) as error:
            await transport.request("GET", "https://finance.yahoo.com/test")
        assert error.value.retry_after_seconds == 300
    finally:
        transport.close()


async def test_schema_failure_allows_query_fallback(tmp_path):
    requests = []

    def handler(request):
        requests.append(request)
        return (
            httpx.Response(200, text="not JSON")
            if len(requests) == 1
            else httpx.Response(200, json={"news": []})
        )

    transport, _ = make_transport(handler)
    try:
        context, _ = _context(tmp_path, "yahoo_finance_news", {})
        async with httpx.AsyncClient() as client:
            result = await YahooFinanceNewsAdapter(
                DoxAgentSettings(_env_file=None), client, transport=transport
            ).poll(context)
        assert result.acquisition_metadata["query_mode"] == "finance_search_fallback"
        assert len(requests) == 2
    finally:
        transport.close()
