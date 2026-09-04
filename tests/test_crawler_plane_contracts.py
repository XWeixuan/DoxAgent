from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from doxagent.crawler_plane.assets import CrawlerAssetStore
from doxagent.crawler_plane.quality import quality_summary, quality_warnings
from doxagent.crawler_plane.runtime import (
    ParentNetworkSession,
    PlaywrightBrowserRuntime,
    unlimited_request_permit,
)
from doxagent.crawler_plane.schema import CrawlerItemFailure, NetworkMode
from doxagent.crawler_plane.worker_runtime import CrawlerHttpClient


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_url"),
    [
        (
            {
                "method": "GET",
                "url": (
                    "https://www.federalregister.gov/api/v1/documents.json?"
                    "conditions%5Bagencies%5D%5B%5D=industry-and-security-bureau&"
                    "conditions%5Btopics%5D%5B%5D=semiconductors&"
                    "conditions%5Btopics%5D%5B%5D=exports&term=&q=a%20b"
                ),
                "headers": {},
            },
            (
                "https://www.federalregister.gov/api/v1/documents.json?"
                "conditions%5Bagencies%5D%5B%5D=industry-and-security-bureau&"
                "conditions%5Btopics%5D%5B%5D=semiconductors&"
                "conditions%5Btopics%5D%5B%5D=exports&term=&q=a%20b"
            ),
        ),
        (
            {
                "method": "GET",
                "url": "https://example.test/feed?a=1&old=yes",
                "params": {},
                "headers": {},
            },
            "https://example.test/feed",
        ),
        (
            {
                "method": "GET",
                "url": "https://example.test/feed?a=1&old=yes",
                "params": {"agency": "BIS", "term": "semiconductor rule"},
                "headers": {},
            },
            "https://example.test/feed?agency=BIS&term=semiconductor+rule",
        ),
    ],
)
async def test_http_broker_distinguishes_omitted_and_explicit_params(
    tmp_path: Path,
    payload: dict[str, Any],
    expected_url: str,
) -> None:
    seen: list[str] = []

    async def respond(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, text="ok", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(respond))
    session = ParentNetworkSession(
        execution_id="exec-query",
        crawler_id="query-contract",
        crawler_version=1,
        mode=NetworkMode.RECORD,
        asset_store=CrawlerAssetStore(tmp_path / "crawler-plane"),
        request_permit=unlimited_request_permit,
        client=client,
        browser=PlaywrightBrowserRuntime(),
    )
    try:
        await session.handle("http", payload)
    finally:
        await client.aclose()

    assert seen == [expected_url]
    assert session.exchanges[0].request_url == expected_url


def test_retry_payload_is_bounded() -> None:
    with pytest.raises(ValueError, match="retry_payload exceeds"):
        CrawlerItemFailure(
            item_key="item",
            stage="detail",
            url="https://example.test/item",
            error_code="temporary",
            error_message="failed",
            retry_payload={"body": "x" * 70_000},
        )


@pytest.mark.asyncio
async def test_child_http_client_preserves_omitted_params_contract() -> None:
    payloads: list[dict[str, Any]] = []

    class _Broker:
        async def request(self, op: str, payload: dict[str, Any]) -> dict[str, Any]:
            assert op == "http"
            payloads.append(payload)
            return {
                "status_code": 200,
                "url": payload["url"],
                "headers": {},
                "body": "ok",
            }

    client = CrawlerHttpClient(_Broker())  # type: ignore[arg-type]
    await client.get("https://example.test/feed?a=1&a=2&blank=&q=a%20b")
    await client.get("https://example.test/feed?a=1", params={})

    assert "params" not in payloads[0]
    assert payloads[1]["params"] == {}


def test_quality_summary_warns_without_changing_execution_contract() -> None:
    class _Observation:
        def __init__(self, body: str) -> None:
            self.body = body

    summary = quality_summary(
        [
            _Observation("Navigation\nSame publication body"),
            _Observation("Navigation\nSame publication body"),
            _Observation("Navigation\nSame publication body"),
        ]
    )

    assert summary["observation_count"] == 3
    assert summary["duplicate_body_ratio"] == pytest.approx(2 / 3, abs=0.0001)
    assert quality_warnings(summary) == ["possible_template_noise"]
