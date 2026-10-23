"""Bounded read-only HTTP/browser coverage comparison using the current Site owner.

Run inside the Message Bus container; never queues, admits, or publishes a message.
Output contains IDs/timestamps/costs, not Cookies, tokens or downloaded body text.
"""

import asyncio
import json
import time
import uuid
from datetime import UTC, datetime

import httpx

from doxagent.message_bus_v2.market_sources import parse_globenewswire, parse_investorshub
from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.tokens import read_token


async def main():
    settings = DoxAgentSettings()
    token = read_token(settings.site_access_worker_token, settings.site_access_worker_token_file)
    report = {
        "at": datetime.now(UTC).isoformat(),
        "mode": "read-only, current deployed runtime",
        "samples": [],
    }
    urls = [
        (
            "globenewswire_search",
            "MU",
            "https://www.globenewswire.com/en/search/keyword/Micron/load/more?page=1&pageSize=10",
            parse_globenewswire,
        ),
        (
            "globenewswire_search",
            "INTC",
            "https://www.globenewswire.com/en/search/keyword/Intel/load/more?page=1&pageSize=10",
            parse_globenewswire,
        ),
        (
            "investorshub_ticker_news",
            "MU",
            "https://investorshub.advfn.com/stock-market/NASDAQ/MU/news",
            parse_investorshub,
        ),
    ]
    async with (
        httpx.AsyncClient(timeout=35, follow_redirects=True) as public,
        httpx.AsyncClient(
            base_url=settings.site_access_url,
            headers={
                "Authorization": "Bearer " + token,
                "X-Site-Access-Protocol": "3",
                "X-Site-Access-Capture-Metadata": "1",
            },
            timeout=55,
        ) as site,
    ):
        for source, ticker, url, parser in urls:
            sample = {"source": source, "ticker": ticker, "url": url}
            parsed = []
            started = time.monotonic()
            try:
                response = await public.get(url, timeout=8)
                sample["http_status"] = response.status_code
                if response.status_code == 200:
                    parsed = parser(
                        response.text,
                        now=datetime.now(UTC),
                        **({"ticker": ticker} if "investorshub" in source else {}),
                    )
                sample["http_ids"] = [[m.external_id, m.published_at.isoformat()] for m in parsed]
            except Exception as exc:
                sample["http_error"] = type(exc).__name__
            sample["http_ms"] = round((time.monotonic() - started) * 1000)
            started = time.monotonic()
            try:
                response = await site.post(
                    "/v1/access/execute",
                    json={
                        "request_id": "probe-" + uuid.uuid4().hex,
                        "operation_id": "latency-probe-" + uuid.uuid4().hex,
                        "purpose": "CRAWLER",
                        "origin_scope_id": "source:" + source,
                        "url": url,
                        "mode": "BROWSER",
                        "recipe_parameters": {"ticker": ticker},
                        "remaining_budget_ms": 30000,
                    },
                )
                response.raise_for_status()
                result = response.json()
                sample["browser_disposition"] = result["disposition"]
                sample["browser_reason"] = result.get("reason_code")
                sample["browser_queue_ms"] = result.get("queue_wait_ms")
                sample["browser_runtime_ms"] = result.get("network_ms")
                if result["disposition"] == "SUCCESS":
                    browser_rows = parser(
                        result["body"],
                        now=datetime.now(UTC),
                        **({"ticker": ticker} if "investorshub" in source else {}),
                    )
                    sample["browser_ids"] = [
                        [m.external_id, m.published_at.isoformat()] for m in browser_rows
                    ]
                    sample["equal_ids_and_times"] = sample.get("http_ids") == sample["browser_ids"]
            except Exception as exc:
                sample["browser_error"] = type(exc).__name__
            sample["browser_ms"] = round((time.monotonic() - started) * 1000)
            report["samples"].append(sample)
    # Current auth/capability evidence is reported by the Site owner, not inferred from old flags.
    admin_token = read_token(
        settings.site_access_admin_token, settings.site_access_admin_token_file
    )
    if not admin_token:
        report["authenticated_identities"] = "not available to worker role"
        print(json.dumps(report, ensure_ascii=False, default=str))
        return
    async with httpx.AsyncClient(
        base_url=settings.site_access_url,
        headers={"Authorization": "Bearer " + admin_token},
        timeout=15,
    ) as admin:
        response = await admin.get("/v1/identities")
        response.raise_for_status()
        report["authenticated_identities"] = [
            {k: value.get(k) for k in ("spec", "runtime", "site_auth")}
            for value in response.json()
            if any(
                name in str(value.get("spec", {}).get("identity_id", ""))
                for name in ("reuters", "digitimes")
            )
        ]
        for value in report["authenticated_identities"]:
            spec = value.get("spec") or {}
            value["spec"] = {
                k: spec.get(k) for k in ("identity_id", "revision", "profile_id", "egress_id")
            }
    print(json.dumps(report, ensure_ascii=False, default=str))


if __name__ == "__main__":
    asyncio.run(main())
