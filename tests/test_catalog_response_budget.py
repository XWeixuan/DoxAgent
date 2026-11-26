"""The real API envelope must admit a full catalog before MCP can page it."""

import copy
import json

import httpx
import pytest

from doxagent.agent_mcp.client import MAX_RESULT_BYTES, ApiClient, encoded
from doxagent.agent_mcp.service import AgentReads
from doxagent.api_v2.app import PREFIX
from tests.test_agent_mcp_readonly import TOKEN, backend  # noqa: F401
from tests.test_source_catalog import CALENDAR, NOW, catalog_data, seed


def large_catalog(tmp_path, monkeypatch):
    monkeypatch.setattr("doxagent.api_v2.source_catalog.store_from_environment", lambda: None)
    catalog = catalog_data(seed(tmp_path / "catalog"), CALENDAR, NOW)
    template = catalog["sources"][0]
    catalog["sources"] = [
        {**copy.deepcopy(template), "source_id": f"s{i:03}", "name": f"S{i:03}",
         "description": "巡检目录" * 300}
        for i in range(108)
    ]
    assert 262144 < len(json.dumps(catalog, ensure_ascii=False).encode()) < 1048576
    monkeypatch.setattr("doxagent.api_v2.source_catalog.catalog_data", lambda *a, **k: catalog)
    return catalog


@pytest.mark.asyncio
async def test_large_catalog_survives_http_envelope_and_mcp_snapshot_pages(
    backend, tmp_path, monkeypatch,  # noqa: F811
):
    app, _, _, _ = backend
    catalog = large_catalog(tmp_path, monkeypatch)
    requests = []

    async def record(request):
        requests.append(request.url.path)

    api = ApiClient("http://local", TOKEN, transport=httpx.ASGITransport(app=app))
    api.client.event_hooks["request"] = [record]
    reads = AgentReads(api)
    found, cursor, snapshots = [], None, set()
    try:
        while True:
            result = await reads.call("doxagent_get_overview", {
                "section": "sources", "limit": 20, **({"cursor": cursor} if cursor else {}),
            })
            assert len(encoded(result)) <= MAX_RESULT_BYTES
            assert len(result["data"]["items"]) <= 20
            found.extend(result["data"]["items"])
            snapshots.add(result["data"]["snapshot_id"])
            cursor = result["data"]["next_cursor"]
            assert result["data"]["has_more"] == bool(cursor)
            if not cursor:
                break
    finally:
        await api.close()
    assert len(found) == len({r["source_id"] for r in found}) == 108
    assert [r["source_id"] for r in found] == [r["source_id"] for r in catalog["sources"]]
    assert all(r["description"] == "巡检目录" * 300 for r in found)
    assert snapshots == {catalog["snapshot_at"]}
    assert requests == [PREFIX + "/message-bus/source-catalog"]


@pytest.mark.asyncio
async def test_catalog_still_rejects_above_its_own_hard_cap(
    backend, tmp_path, monkeypatch,  # noqa: F811
):
    app, _, _, _ = backend
    catalog = large_catalog(tmp_path, monkeypatch)
    catalog["sources"][0]["description"] = "大" * 400000
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="http://local") as client:
        response = await client.get(PREFIX + "/message-bus/source-catalog",
                                    headers={"Authorization": "Bearer " + TOKEN})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "RESOURCE_TOO_LARGE"


@pytest.mark.asyncio
async def test_ordinary_list_keeps_256k_budget(backend):  # noqa: F811
    from fastapi import Request

    app, _, _, _ = backend

    @app.get(PREFIX + "/message-bus/source-catalog-budget-control")
    async def ordinary(request: Request):
        # Use a real list DTO and response boundary, with a valid large title.
        from tests.test_agent_mcp_readonly import page
        from tests.v2_backend.test_trade_outcomes import case, rollup_case_trade
        row = rollup_case_trade(case(), [])
        row["title"] = {"state": "AVAILABLE", "value": "长" * 100000, "reason": None}
        return app.state.respond(request, "CaseSummaryPage", page([row]))

    # The control path is intentionally outside the agent's read scope.
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                base_url="http://local") as client:
        response = await client.get(PREFIX + "/message-bus/source-catalog-budget-control",
                                    headers={"Authorization": "Bearer offline"})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "RESOURCE_TOO_LARGE"
