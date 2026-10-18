import json
from datetime import UTC, datetime

import pytest

from doxagent.codex_worker.w3_market import compile_series
from doxagent.persistent_runtime_v2.w3_research_store import (
    W3ResearchStore,
    business,
    columnar,
    compact,
)


def fixture_store(tmp_path, *, length=30000):
    store = W3ResearchStore(tmp_path, page_bytes=3500, page_tokens=4096)
    unit = dict(
        name="需求",
        scope="公司",
        horizon="两年",
        ref=["【cite:O1】"],
        state={
            "values": [{"value": 0, "previous_value": None, "as_of": "2026-10-01"}, {"value": 1}]
        },
        expectation_baseline=[{"name": "基线", "baseline": "中" * length}],
        realization_factors=[],
        potential_gaps=[],
        unknown_business="保留未知字段",
    )
    components = store.install(
        {
            "d2": compact({"shells": [dict(name="需求Shell", scope="边界", units=[unit])]}),
            "policy": compact(
                {
                    "policies": [
                        {
                            "policy_id": "P1",
                            "ref": {"shell_id": "需求Shell"},
                            "activation_conditions": [],
                        }
                    ]
                }
            ),
        },
        "MU",
    )
    manifest = dict(
        case_id="case1",
        ticker="MU",
        components=components,
        policy_usage=[{"policy_id": "P1", "consumed": True}],
        state={},
    )
    binding = store.bind("case1", "trade_decision", manifest)
    return store, binding["context_ref"], manifest


def test_complete_pages_exact_join_and_context_isolation(tmp_path):
    store, context, manifest = fixture_store(tmp_path)
    args = dict(context_ref=context, objects=[{"kind": "d2_unit", "id": "需求"}])
    pages, cursor = [], None
    while True:
        page = store.call("read", {**args, **({"cursor": cursor} if cursor else {})})
        assert len(compact(page).encode()) <= 3500
        assert page["receipt"].startswith("END ")
        pages.append(page["body"])
        cursor = page["next_cursor"]
        if page["page"]["complete"]:
            assert cursor is None
            break
    result = json.loads("".join(pages))[0]
    assert result["body"]["unit"]["unknown_business"] == "保留未知字段"
    assert len(pages) > 10
    other = store.bind(
        "case2", "trade_decision", {**manifest, "case_id": "case2", "policy_usage": []}
    )
    first = store.call("read", args)
    with pytest.raises(ValueError):
        store.call(
            "read", {**args, "context_ref": other["context_ref"], "cursor": first["next_cursor"]}
        )
    with pytest.raises(PermissionError):
        store.call("catalog", {"context_ref": "wrong"})
    with pytest.raises(ValueError):
        store.bind("case1", "trade_decision", {**manifest, "ticker": "BE"})


def test_policy_discovery_and_same_read_usage_sources(tmp_path):
    store, context, manifest = fixture_store(tmp_path, length=20)
    result = json.loads(
        store.call("read", dict(context_ref=context, objects=[dict(kind="policy", id="P1")]))[
            "body"
        ]
    )[0]
    assert result["frozen_usage"]["consumed"] is True
    sources = json.loads(
        store.call(
            "read",
            dict(context_ref=context, view="sources", objects=[dict(kind="policy", id="P1")]),
        )["body"]
    )[0]
    assert sources["body"]["/ref"]["shell_id"] == "需求Shell"
    before = sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("objects.json"))
    store.bind("case2", "trade_decision", {**manifest, "case_id": "case2"})
    assert before == sorted(p.relative_to(tmp_path) for p in tmp_path.rglob("objects.json"))


def test_columnar_missing_not_equal_null_and_preserves_values():
    refs = {}
    value = business({"rows": [{"n": 0, "p": None}, {"n": 1}], "ref": ["x"]}, refs)
    converted = columnar(value)["rows"]
    assert converted["missing"] == [[], [1]]
    assert converted["rows"][0] == [0, None]
    assert refs == {"/ref": ["x"]}


def test_d1_main_sections_are_not_confused_with_sibling_headings(tmp_path):
    store = W3ResearchStore(tmp_path)
    components = store.install(
        {
            "d1": "# MU\n## C1 Fundamentals\n## 一、状态\nC1data\n"
            "## C3 Industry\n## 一、状态\nC3data"
        },
        "MU",
    )
    ctx = store.bind("case", "trade_decision", {"components": components})["context_ref"]
    body = json.loads(
        store.call("read", {"context_ref": ctx, "objects": [{"kind": "d1_section", "id": "C1"}]})[
            "body"
        ]
    )[0]["body"]
    assert "C1data" in body and "C3data" not in body


def test_market_four_hour_clipping_and_forming_source_separation():
    now = datetime(2026, 10, 16, 5, 32, 30, tzinfo=UTC)
    bars = [
        dict(date=str(int(now.timestamp()) - offset * 60), open=1, high=2, low=0, close=1, volume=3)
        for offset in (242, 237, 7, 2)
    ]
    result = compile_series(
        [("OVERNIGHT", {"bars": bars}), ("SMART", {"bars": bars})], now=now, hours=4, minutes=5
    )
    assert len(result["rows"]) == 2
    assert len(result["forming"]) == 1
    assert all(row[-1] == "OVERNIGHT" for row in result["rows"])


@pytest.mark.asyncio
async def test_resident_http_mcp_real_protocol_auth_and_single_body(tmp_path):
    import httpx

    from doxagent.mcp.w3_research_server import ResearchMcp

    store, context, _ = fixture_store(tmp_path, length=10)
    mcp = ResearchMcp(store, "s" * 32)
    async with mcp.manager.run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=mcp), base_url="http://localhost"
        ) as client:
            denied = await client.post("/", json={})
            assert denied.status_code == 401
            headers = {
                "Authorization": "Bearer " + mcp.token,
                "Accept": "application/json, text/event-stream",
                "X-W3-Capsule": "capsule",
            }
            initialized = await client.post(
                "/",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2025-03-26",
                        "capabilities": {},
                        "clientInfo": {"name": "test", "version": "1"},
                    },
                },
            )
            assert initialized.status_code == 200, initialized.text
            headers["MCP-Protocol-Version"] = initialized.json()["result"]["protocolVersion"]
            listed = await client.post(
                "/", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
            )
            assert len(listed.json()["result"]["tools"]) == 4
            assert "capsule" in mcp.ready
            read = await client.post(
                "/",
                headers=headers,
                json={
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {
                        "name": "read",
                        "arguments": {
                            "context_ref": context,
                            "objects": [{"kind": "policy", "id": "P1"}],
                        },
                    },
                },
            )
            result = read.json()["result"]
            assert len(result["content"]) == 1
            assert "structuredContent" not in result or not result["structuredContent"]
            assert json.loads(result["content"][0]["text"])["page"]["complete"]


def test_new_runner_keeps_only_controls_and_freezes_phase_binding(tmp_path):
    from doxagent.persistent_runtime_v2.journal import RuntimeJournal
    from doxagent.persistent_runtime_v2.w3 import CodexW3AgentRunner
    from tests.test_persistent_runtime_w3 import _Context, _Workspace
    from tests.test_runtime_policy_direction_usage import fixture_case
    from tests.test_w3_case_models import ModelWorker

    store = W3ResearchStore(tmp_path / "shared")

    class Worker(ModelWorker):
        async def prepare_w3_research(self, ticker, components):
            return store.install(components, ticker)

        async def bind_w3_context(self, case_id, phase, manifest):
            return store.bind(case_id, phase, manifest)

    case, _ = fixture_case()
    case.frozen_inputs.update(
        w3_assets={"w3/AGENTS.md": "agents", "w3/skills/skill.md": "skill"},
        w3_contracts={"w3_entry_version": "w3-entry-v5"},
    )
    workspace, worker = _Workspace(), Worker()
    runner = CodexW3AgentRunner(
        worker=worker,
        workspace=workspace,
        context_provider=_Context(),
        prompt_root=tmp_path,
        journal=RuntimeJournal(tmp_path / "runtime.sqlite3"),
        timeout_seconds=30,
    )
    try:
        runner.prepare_public_assets(case)
        context = runner.prepare_case(case)
        runner.run_phase(
            case=case,
            record={"record_id": "r", "workspace": "w3-ready-owned", "thread_id": "child"},
            phase="trade_decision",
            payload={"policies": [], "policy_usage": []},
            context=context,
        )
        request = worker.requests[-1]
        task = json.loads(request.prompt)
        assert task["context_ref"].startswith("w3ctx_")
        assert request.research_assets_mcp_enabled and not request.data_mcp_enabled
        assert "research_path" not in task and "reference_path" not in task
        assert not any(
            "context/research" in key or "reference_view.md" in key for key in workspace.files
        )
        assert not (tmp_path / "w3-assets").exists()
    finally:
        runner.close()


@pytest.mark.asyncio
async def test_market_cursor_keeps_read_snapshot_and_honors_small_outer_budget(tmp_path):
    from mcp import types

    from doxagent.mcp.w3_research_server import ResearchMcp

    class Market:
        calls = 0

        def view(self, manifest, args):
            self.calls += 1
            return {
                "view_version": "same-bars",
                "rows": ["中文测试" * 50] * 20,
                "observed_at": str(self.calls),
            }

    store = W3ResearchStore(tmp_path)
    context = store.bind("case", "trade_decision", {})["context_ref"]
    market = Market()
    server = ResearchMcp(store, "s" * 32, market)
    cursor, bodies = None, []
    while True:
        args = dict(context_ref=context, max_bytes=1200)
        if cursor:
            args["cursor"] = cursor
        result = await server.call_tool(
            None, types.CallToolRequestParams(name="market_view", arguments=args)
        )
        text = result.content[0].text
        assert len(text.encode()) <= 1200, text
        assert not result.is_error
        page = json.loads(text)
        bodies.append(page["body"])
        cursor = page["next_cursor"]
        if not cursor:
            break
    view = json.loads("".join(bodies))
    assert view["observed_at"] == "1" and len(view["rows"]) == 20
    assert market.calls == 1


def test_d1_read_removes_citation_transport_and_retains_sources(tmp_path):
    store = W3ResearchStore(tmp_path)
    components = store.install(
        {"d1": "## C1\nEconomic claim【cite:O1】 [release](https://example.com/release)"}, "MU"
    )
    token = store.bind("c", "trade_decision", {"components": components})["context_ref"]
    args = dict(context_ref=token, objects=[dict(kind="d1_section", id="C1")])
    body = json.loads(store.call("read", args)["body"])[0]["body"]
    assert "Economic claim" in body and "https://" not in body and "【cite:" not in body
    sources = store.call("read", {**args, "view": "sources"})["body"]
    assert "https://example.com/release" in sources and "O1" in sources


def test_index_failure_keeps_exact_original_readable_without_workspace_copy(tmp_path):
    store = W3ResearchStore(tmp_path)
    raw = '{"shells":[{"name":"old-shell","units":[{"unknown_legacy_field":"retain"}]}]}'
    components = store.install({"d2": raw}, "MU")
    token = store.bind("case", "trade_decision", {"components": components})["context_ref"]
    page = store.call("read", dict(context_ref=token, objects=[dict(kind="raw_document", id="d2")]))
    assert json.loads(page["body"])[0]["body"] == raw
    assert not (tmp_path / "case").exists()


@pytest.mark.asyncio
async def test_local_backtest_uses_same_resident_mcp_with_frozen_files_and_no_broker(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace

    from doxagent.codex_worker.job_store import JobStore
    from doxagent.settings import DoxAgentSettings
    from doxagent.w3_backtest.runner import _LocalWorker

    def forbidden(*args, **kwargs):
        raise AssertionError("offline replay reached IBKR")

    monkeypatch.setattr("doxagent.codex_worker.w3_market.MarketProvider.history", forbidden)

    class Manager:
        store = JobStore(tmp_path)
        _runtime = SimpleNamespace(
            runtime=SimpleNamespace(_settings=DoxAgentSettings(_env_file=None))
        )

        async def close(self):
            pass

    worker = _LocalWorker(Manager())
    try:
        components = await worker.prepare_w3_research("MU", {"d1": "# C1\nfrozen"})
        binding = await worker.bind_w3_context(
            "c",
            "trade_decision",
            {"case_id": "c", "ticker": "MU", "mode": "OFFLINE_REPLAY", "components": components},
        )
        await worker.start_w3_market({"case_id": "c", "mode": "OFFLINE_REPLAY"})
        assert worker.server.started
        assert Manager._runtime.runtime._settings.w3_research_mcp_url.startswith(
            "http://127.0.0.1:"
        )
        body = worker.research.call(
            "read",
            {
                "context_ref": binding["context_ref"],
                "objects": [{"kind": "d1_section", "id": "C1"}],
            },
        )["body"]
        assert "frozen" in body
    finally:
        await worker.aclose()
