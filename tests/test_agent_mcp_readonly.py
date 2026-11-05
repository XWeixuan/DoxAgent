"""Presentation boundaries, real API integration and the actual stdio protocol."""

import asyncio
import copy
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from pydantic import ValidationError

from doxagent.agent_mcp import projection as p
from doxagent.agent_mcp.client import MAX_RESULT_BYTES, ApiClient, ReadError, StateStore, encoded
from doxagent.agent_mcp.schemas import TOOLS, DetailInput, OverviewInput, SearchInput
from doxagent.agent_mcp.service import AgentReads
from doxagent.api_v2.app import PREFIX, create_app
from doxagent.api_v2.auth import Principal
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.v2_control.repository import ControlRepository
from doxagent.v2_read.projectors import state_wire
from doxagent.v2_read.repository import ReadStore
from tests.v2_backend.test_api import OfflineAuth
from tests.v2_backend.test_streaming import message
from tests.v2_backend.test_trade_outcomes import case, rollup_case_trade, value

TOKEN = "agent-read-fixture-credential-0123456789"
VIEW = "a" * 32 + "." + "b" * 32 + "." + str(int(time.time()) + 800)


def wire(payload, *, view=VIEW, resource=True):
    return {
        "data": {
            "state": "AVAILABLE",
            "data": payload,
            "reason": None,
            "coverage": {"state": "COMPLETE", "reasons": []},
        }
        if resource
        else payload,
        "meta": {
            "view_id": view,
            "as_of": "2026-11-04T00:00:00Z",
            "freshness": "FRESH",
            "refresh_error": None,
            "request_id": "request-fixture",
        },
    }


def page(items, next_cursor=None):
    return {
        "items": items,
        "next_cursor": next_cursor,
        "has_more": bool(next_cursor),
        "snapshot_id": VIEW,
        "limit": 20,
    }


def context(page_kind="RUNTIME", ticker="MU", selected="ALL"):
    return {
        "view_id": VIEW,
        "page": page_kind,
        "ticker": ticker,
        "period": {"selected": selected},
        "period_options": [],
    }


@pytest.fixture
def backend(tmp_path, monkeypatch):
    credential = tmp_path / "read-token"
    credential.write_text(TOKEN, encoding="utf-8")
    monkeypatch.setenv("DOXAGENT_AGENT_READ_TOKEN_FILE", str(credential))
    monkeypatch.setenv("DOXAGENT_MESSAGE_BUS_V2_COLLECTION_PLANE_ENABLED", "false")
    store = ReadStore(tmp_path / "read.db")
    store.migrate()
    store.migrate_projection_indexes()
    control = ControlRepository(RuntimeJournal(tmp_path / "runtime.db"))
    control.migrate()
    states = []
    for ticker in ("MU", "SNOW"):
        state = state_wire(
            {
                "ticker": ticker,
                "analysis_allowed": True,
                "removed": False,
                "requested_mode": "MESSAGE_MONITORING",
                "mode": "MESSAGE_MONITORING",
                "activation_id": "active",
                "status": "RUNNING",
                "initialization_id": None,
                "removed_at": None,
                "revision": 1,
                "epoch": 1,
                "cutoff_at": None,
            }
        )
        states.append({"kind": "ticker", "ticker": ticker, "id": ticker, "data": state})
    ref = store.put_content("MU", "Case 正文" * 5000)
    research = store.put_content("MU", "unrelated research")
    summary = rollup_case_trade(case(), [])
    member = message("news")
    member.update(parent="case-1")
    member["data"]["body"] = ref
    store.ingest(
        "fixture",
        "seed",
        states
        + [
            {
                "kind": "case",
                "ticker": "MU",
                "id": "case-1",
                "day": "2026-09-29",
                "sort": "2026-09-29T09:00:00Z",
                "data": summary,
            },
            member,
        ],
    )
    app = create_app(store=store, control=control, auth=OfflineAuth())
    return app, store, ref, research


def test_read_token_is_closed_and_normal_auth_keeps_existing_permissions(backend):
    app, _, _, _ = backend
    headers = {"Authorization": "Bearer " + TOKEN}
    with TestClient(app) as client:
        assert (
            client.get(PREFIX + "/auth/me", headers=headers).json()["data"]["can_operate"] is False
        )
        ordinary = client.get(PREFIX + "/auth/me", headers={"Authorization": "Bearer offline"})
        assert ordinary.json()["data"]["can_operate"] is True
        for path in ("/tickers/MU/pause", "/audit/revenue/refresh", "/audit/revenue/hypotheticals"):
            assert client.post(PREFIX + path, headers=headers, json={}).status_code == 403
        for method, path in (
            ("patch", "/tickers/MU/bindings/news"),
            ("delete", "/tickers/MU"),
            ("get", "/tickers/MU/research"),
            ("get", "/tickers/MU/events"),
            ("get", "/tickers/MU/runtime/graph"),
        ):
            assert getattr(client, method)(PREFIX + path, headers=headers).status_code == 403
        for kind in ("RESEARCH", "EXPECTATIONS", "EVENTS"):
            assert (
                client.get(
                    PREFIX + "/read-context", params={"page": kind, "ticker": "MU"}, headers=headers
                ).status_code
                == 403
            )
        assert (
            client.get(PREFIX + "/overview/metrics?limit=100", headers=headers).status_code == 422
        )
        assert (
            client.get(PREFIX + "/auth/me", headers={"Authorization": "Bearer bad"}).status_code
            == 401
        )


@pytest.mark.asyncio
async def test_read_scope_is_enforced_inside_query_worker_too(backend):
    original, store, _, _ = backend

    class ParentVerified:
        async def authenticate(self, _token):
            return Principal("agent-read", "AGENT_READ", time.time() + 60, read_only=True)

    child = create_app(
        store=store, control=original.state.control, auth=ParentVerified(), query_worker=True
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=child), base_url="http://local"
    ) as client:
        response = await client.post(
            PREFIX + "/tickers/MU/pause",
            headers={"Authorization": "Bearer parent-verified"},
            json={},
        )
        assert response.status_code == 403


def test_content_is_case_bound_and_continues_only_in_same_view(backend):
    app, _, body, research = backend
    headers = {"Authorization": "Bearer " + TOKEN}
    with TestClient(app) as client:
        view = client.get(
            PREFIX + "/read-context",
            params={"page": "RUNTIME", "ticker": "MU", "period": "ALL"},
            headers=headers,
        ).json()["data"]["view_id"]
        root = PREFIX + "/tickers/MU/contents/"
        params = {"view_id": view, "case_id": "case-1"}
        assert client.get(root + body["content_id"], headers=headers).status_code >= 400
        assert (
            client.get(root + research["content_id"], params=params, headers=headers).status_code
            == 403
        )
        first = client.get(root + body["content_id"], params=params, headers=headers)
        assert first.status_code == 200, first.text
        chunk = first.json()["data"]["data"]
        assert len(chunk["text"].encode()) <= 16000 and not chunk["complete"]
        next_params = {**params, "cursor": chunk["next_cursor"]}
        assert (
            client.get(root + body["content_id"], params=next_params, headers=headers).json()[
                "data"
            ]["data"]["chunk_index"]
            == 1
        )
        other = client.get(
            PREFIX + "/read-context",
            params={"page": "RUNTIME", "ticker": "MU", "period": "ALL"},
            headers=headers,
        ).json()["data"]["view_id"]
        assert (
            client.get(
                root + body["content_id"], params={**next_params, "view_id": other}, headers=headers
            ).status_code
            == 400
        )


@pytest.mark.asyncio
async def test_real_api_overview_and_search_reuse_bounded_queries(backend):
    app, store, _, _ = backend
    api = ApiClient("http://local", TOKEN, transport=httpx.ASGITransport(app=app))
    reads = AgentReads(api)
    try:
        result = await reads.call("doxagent_get_overview", {"period": "TRADING_DAYS_7", "limit": 1})
        row = result["data"]["items"][0]
        assert row["overview"]["state"]["ticker"] == "MU"
        assert "actions" not in row["overview"]["state"]
        assert "previous" not in result["data"]["metrics"]["data"]["messages"]
        assert "normal_tickers" not in encoded(result).decode()
        assert row["runtime"]["data"].keys() >= {"processed_cases", "hot_path_mean_seconds"}
        assert "sources" not in row["bus"]["data"]
        assert result["data"]["has_more"]
        next_page = await reads.call(
            "doxagent_get_overview",
            {"period": "TRADING_DAYS_7", "limit": 1, "cursor": result["data"]["next_cursor"]},
        )
        assert next_page["data"]["items"][0]["overview"]["state"]["ticker"] == "SNOW"
        assert not next_page["data"]["has_more"]
        sources = await reads.call("doxagent_get_overview", {"section": "sources", "ticker": "MU"})
        assert sources["data"]["items"] == []
        search = await reads.call(
            "doxagent_search_cases", {"ticker": "MU", "period": "ALL", "q": "News"}
        )
        assert search["data"]["items"][0]["case_id"] == "case-1"
        assert "final_policy_hit" not in search["data"]["items"][0]
        assert "body" not in search["data"]["items"][0]
        with store.connect() as db:
            assert db.execute("SELECT COUNT(*) FROM views").fetchone()[0] == 3
    finally:
        await api.close()


def test_closed_schemas_and_display_projection_do_not_leak_new_dto_fields():
    for raw in ({"ticker": "MU", "q": " "}, {"ticker": "MU", "q": "x", "path": "/shell"}):
        with pytest.raises(ValidationError):
            SearchInput.model_validate(raw)
    with pytest.raises(ValidationError):
        OverviewInput(period="ALL")
    with pytest.raises(ValidationError):
        DetailInput(ticker="MU", case_id="c", part="attempts")
    row = rollup_case_trade(case(), [])
    row["secret_added_by_future_dto"] = "never disclose"
    assert "secret_added_by_future_dto" not in p.case_row(row)
    assert p.case_row(row)["trade"].keys() == {"state", "reason_codes"}
    assert (
        p.watch_pin(
            {
                "policy_watch": {
                    "followup": [],
                    "actions": [],
                    "receipt": {"hidden_while_closed": True},
                }
            }
        )
        == {}
    )


@pytest.mark.asyncio
async def test_codex_cost_is_read_only_and_not_priced(backend):
    app, store, _, _ = backend
    with store.connect() as db:
        before = store.highwater(db)
    api = ApiClient("http://local", TOKEN, transport=httpx.ASGITransport(app=app))
    try:
        result = await AgentReads(api).call(
            "doxagent_get_overview",
            {"section": "cost", "scope": "CODEX", "period": "TRADING_DAYS_7"},
        )
        assert result["data"]["totals"]["total_cost_usd"]["current"]["state"] == "NOT_APPLICABLE"
        assert "rows" not in result["data"] and "trend" not in result["data"]
        with store.connect() as db:
            assert store.highwater(db) == before
    finally:
        await api.close()


@pytest.mark.asyncio
async def test_twenty_ticker_overview_fits_or_continues_without_omission(backend):
    app, store, _, _ = backend
    base = store.get("ticker", "MU", "MU")
    store.ingest(
        "fixture",
        "twenty-tickers",
        [
            {
                "kind": "ticker",
                "ticker": f"T{i:02}",
                "id": f"T{i:02}",
                "data": {**base, "ticker": f"T{i:02}"},
            }
            for i in range(18)
        ],
    )
    api = ApiClient("http://local", TOKEN, transport=httpx.ASGITransport(app=app))
    reads = AgentReads(api)
    tickers, cursor = [], None
    try:
        while True:
            result = await reads.call(
                "doxagent_get_overview",
                {"period": "TRADING_DAYS_7", **({"cursor": cursor} if cursor else {})},
            )
            assert len(encoded(result)) <= MAX_RESULT_BYTES
            tickers += [row["overview"]["state"]["ticker"] for row in result["data"]["items"]]
            cursor = result["data"]["next_cursor"]
            if cursor is None:
                break
        assert len(tickers) == len(set(tickers)) == 20
    finally:
        await api.close()


class FakeApi:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    async def get(self, path, params=None):
        self.calls.append((path, copy.deepcopy(params)))
        return copy.deepcopy(self.handler(path, params or {}))


@pytest.mark.asyncio
async def test_oversized_case_page_has_no_loss_and_never_prefetches_details():
    rows = [
        {**rollup_case_trade(case(), []), "case_id": str(i), "title": value("标题" * 2000)}
        for i in range(20)
    ]

    def handle(path, params):
        if path == "read-context":
            return wire(context(), resource=False)
        assert path.endswith("/runtime/cases")
        return wire(page(rows))

    api = FakeApi(handle)
    reads = AgentReads(api)
    ids, cursor = [], None
    while True:
        result = await reads.call(
            "doxagent_list_cases",
            {"ticker": "MU", "period": "ALL", **({"cursor": cursor} if cursor else {})},
        )
        assert len(encoded(result)) <= MAX_RESULT_BYTES
        ids += [r["case_id"] for r in result["data"]["items"]]
        cursor = result["data"]["next_cursor"]
        if not cursor:
            break
        with pytest.raises(ReadError, match="same filters"):
            await reads.call(
                "doxagent_list_cases",
                {"ticker": "MU", "period": "ALL", "source_id": "changed", "cursor": cursor},
            )
    assert ids == [str(i) for i in range(20)]
    assert len(api.calls) == 2  # The buffered display rows did not reread the API.


@pytest.mark.asyncio
async def test_detail_reads_only_requested_content_and_wraps_embedded_continuation():
    text = "完整判断依据🙂" * 5000
    ref = {
        "content_id": "reason",
        "sha256": "a" * 64,
        "content_type": "text/plain",
        "size_bytes": len(text.encode()),
    }
    raw = {
        "summary": case(),
        "messages": page([]),
        "failures": [],
        "document1": {"secret": "not a display field"},
        "w1": wire(
            {
                "novelty": value("NEW"),
                "confidence": value("HIGH"),
                "reasoning": {"state": "AVAILABLE", "data": ref, "reason": None, "coverage": {}},
                "attempts": page([], "backend-attempts"),
            }
        )["data"],
    }

    def handle(path, params):
        if path == "read-context":
            return wire(context(), resource=False)
        if path.endswith("/runtime/cases/case-1"):
            return wire(raw)
        if path.endswith("/attempts"):
            assert params["cursor"] == "backend-attempts" and params["node"] == "W1"
            return wire(
                page(
                    [
                        {
                            "node_id": "W1",
                            "round": "R2",
                            "attempt_number": 2,
                            "status": "SUCCEEDED",
                            "timing": {"wall_seconds": value(1)},
                        }
                    ]
                )
            )
        assert path.endswith("/contents/reason")
        assert params["case_id"] == "case-1" and params["view_id"] == VIEW
        offset = int(params.get("cursor") or 0)
        chunk = text[offset : offset + 1000]
        end = offset + len(chunk)
        return wire(
            {
                "content": ref,
                "text": chunk,
                "chunk_index": offset // 1000,
                "next_cursor": str(end) if end < len(text) else None,
                "complete": end >= len(text),
            }
        )

    api = FakeApi(handle)
    reads = AgentReads(api)
    initial = await reads.call("doxagent_get_case_detail", {"ticker": "MU", "case_id": "case-1"})
    assert len(api.calls) == 2 and "document1" not in initial["data"]
    section = initial["data"]["w1"]["data"]
    continuation = await reads.call(
        "doxagent_get_case_detail",
        {
            "ticker": "MU",
            "case_id": "case-1",
            "part": "attempts",
            "node": "W1",
            "cursor": section["attempts"]["next_cursor"],
        },
    )
    assert continuation["data"]["items"][0]["attempt_number"] == 2
    handle_id = section["reasoning"]["data"]["content_handle"]
    with pytest.raises(ReadError, match="another Case"):
        await reads.call(
            "doxagent_get_case_detail",
            {"ticker": "MU", "case_id": "other", "part": "content", "content_handle": handle_id},
        )
    chunks, cursor = [], None
    while True:
        result = await reads.call(
            "doxagent_get_case_detail",
            {
                "ticker": "MU",
                "case_id": "case-1",
                "part": "content",
                "content_handle": handle_id,
                **({"cursor": cursor} if cursor else {}),
            },
        )
        chunks.append(result["data"]["text"])
        cursor = result["data"]["next_cursor"]
        if result["data"]["complete"]:
            break
    assert "".join(chunks) == text


@pytest.mark.asyncio
async def test_inline_pin_body_chunks_utf8_and_expired_state_is_explicit():
    reads = AgentReads(FakeApi(lambda *_: pytest.fail("inline text must not query the API")))
    text = "🙂中文" * 8000
    short_pin = reads._defer(
        {"body": "short PIN", "receipt": {"status": "ACCEPTED"}},
        {"ticker": "MU", "case_id": "c"},
        VIEW,
    )
    assert "content_handle" in short_pin["body"] and "content_handle" in short_pin["receipt"]
    handle = reads._defer(text, {"ticker": "MU", "case_id": "c"}, VIEW)["content_handle"]
    cursor, pieces = None, []
    while True:
        result = await reads.call(
            "doxagent_get_case_detail",
            {
                "ticker": "MU",
                "case_id": "c",
                "part": "content",
                "content_handle": handle,
                **({"cursor": cursor} if cursor else {}),
            },
        )
        assert len(result["data"]["text"].encode()) <= 16000
        pieces.append(result["data"]["text"])
        cursor = result["data"]["next_cursor"]
        if not cursor:
            break
    assert "".join(pieces) == text
    state = StateStore(clock=lambda: 100)
    expired = state.put("page", {}, 99)
    with pytest.raises(ReadError, match="expired"):
        state.get(expired, "page")


@pytest.mark.asyncio
async def test_queue_cancellation_releases_capacity():
    blocker = asyncio.Event()
    api = FakeApi(lambda *_: None)
    reads = AgentReads(api)

    async def dispatch(*_):
        await blocker.wait()
        return {}

    reads._dispatch = dispatch
    args = {"ticker": "MU"}
    active = asyncio.create_task(reads.call("doxagent_list_cases", args))
    await asyncio.sleep(0)
    queued = [asyncio.create_task(reads.call("doxagent_list_cases", args)) for _ in range(2)]
    await asyncio.sleep(0)
    with pytest.raises(ReadError, match="busy"):
        await reads.call("doxagent_list_cases", args)
    queued[0].cancel()
    with pytest.raises(asyncio.CancelledError):
        await queued[0]
    assert reads.waiting == 1
    blocker.set()
    await asyncio.gather(active, queued[1])
    assert reads.waiting == 0 and not reads.capacity.locked()


@pytest.mark.asyncio
async def test_actual_stdio_discovery_and_tool_call(tmp_path):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            payload = (
                wire(context(), resource=False)
                if "/read-context?" in self.path
                else wire(page([rollup_case_trade(case(), [])]))
            )
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(encoded(payload))

        def log_message(self, *_args):
            pass

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()
    token = tmp_path / "token"
    token.write_text(TOKEN, encoding="utf-8")
    env = {
        **os.environ,
        "DOXAGENT_AGENT_READ_TOKEN_FILE": str(token),
        "DOXAGENT_AGENT_API_URL": f"http://127.0.0.1:{http.server_port}",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
    }
    try:
        params = StdioServerParameters(
            command=sys.executable, args=["-m", "doxagent.agent_mcp"], env=env
        )
        async with stdio_client(params) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                listed = await session.list_tools()
                assert {t.name for t in listed.tools} == set(TOOLS)
                assert all(
                    t.annotations.read_only_hint and not t.annotations.destructive_hint
                    for t in listed.tools
                )
                result = await session.call_tool(
                    "doxagent_list_cases", {"ticker": "MU", "period": "ALL"}
                )
                assert not result.is_error
                assert result.structured_content["data"]["items"][0]["case_id"] == "case-1"
                invalid = await session.call_tool(
                    "doxagent_list_cases", {"ticker": "MU", "command": "shell"}
                )
                assert invalid.is_error
                assert len(calls) == 2
    finally:
        http.shutdown()
        http.server_close()
        thread.join(timeout=2)
