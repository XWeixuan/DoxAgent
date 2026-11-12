import copy

import httpx
import pytest

from doxagent.agent_mcp import projection as p
from doxagent.agent_mcp.client import ApiClient
from doxagent.agent_mcp.schemas import CasesInput
from doxagent.agent_mcp.service import AgentReads
from tests.test_agent_mcp_readonly import TOKEN, backend, wire  # noqa: F401


@pytest.mark.asyncio
async def test_catalog_is_one_live_fetch_then_bounded_snapshot_pages():
    calls = []
    card = dict(source_id="source", name="Source", enabled=True, delivery_mode="production",
                classification={"applicability": "GLOBAL", "sector_ids": [],
                                "listing": {"primary": ["HTTP"], "fallback": ["RSS"]},
                                "body": {"primary": [], "fallback": []}},
                subscriptions=[{"ticker": "MU", "binding_id": "b", "enabled": True}],
                metrics={"mean_capture_seconds": None}, hidden_secret="never expose")
    rows = [{**copy.deepcopy(card), "source_id": f"s{i:02}", "name": f"S{i:02}"}
            for i in range(45)]
    rows[0]["delivery_mode"] = "test"
    rows[1]["enabled"] = False

    class API:
        async def get(self, path, params=None):
            calls.append(path)
            assert path == "message-bus/source-catalog"
            return wire(dict(snapshot_at="2026-11-13T00:00:00Z", semantic_day="2026-11-12",
                             sectors=[], sources=rows))

    reads = AgentReads(API())
    cursor, found = None, []
    while True:
        result = await reads.call("doxagent_get_overview", {
            "section": "sources", "limit": 7, **({"cursor": cursor} if cursor else {}),
        })
        assert len(result["data"]["items"]) <= 7
        assert all("hidden_secret" not in r for r in result["data"]["items"])
        found.extend(r["source_id"] for r in result["data"]["items"])
        cursor = result["data"]["next_cursor"]
        if not cursor:
            break
    assert len(found) == len(set(found)) == 45
    assert found[-2:] == ["s01", "s00"]
    assert calls == ["message-bus/source-catalog"]
    assert result["data"]["items"][-1]["metrics"]["mean_capture_seconds"] is None


def test_new_case_tags_and_null_timing_are_ui_fields():
    CasesInput.model_validate({"ticker": "MU", "result": "LONG_W3_DECISION"})
    row = p.case_row(dict(runtime_tags=["LONG_W3_DECISION"], w3_model_seconds=650,
                          non_model_seconds=None, results=[], hidden="no"))
    assert row["runtime_tags"] == ["LONG_W3_DECISION"]
    assert row["w3_model_seconds"] == 650 and row["non_model_seconds"] is None
    assert "hidden" not in row


@pytest.mark.asyncio
async def test_clock_opt_in_and_parent_worker_header_forwarding(backend):  # noqa: F811
    app, _, _, _ = backend
    api = ApiClient("http://local", TOKEN, transport=httpx.ASGITransport(app=app))
    try:
        context = await api.get("read-context", {"page": "OVERVIEW"})
        assert len(context["data"]["projection"]) == 4
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                    base_url="http://local") as client:
            legacy = await client.get("/api/doxagent/v2/read-context?page=OVERVIEW",
                                      headers={"Authorization": "Bearer " + TOKEN})
            assert "projection" not in legacy.json()["data"]

        class Runner:
            async def run(self, job, **kwargs):
                assert job["headers"]["x-doxagent-projection-clock"] == "1"
                return 200, {}, b'{"data":{},"meta":{}}'

        app.state.query_runner = Runner()
        await api.get("read-context", {"page": "OVERVIEW"})
        app.state.query_runner = None
    finally:
        await api.close()
