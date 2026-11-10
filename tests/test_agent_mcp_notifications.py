"""Fixed-owner routing, honest asynchronous receipts and MCP write annotations."""

import asyncio
import json
from contextlib import suppress

import httpx
import pytest
from mcp import ClientSession
from mcp.shared.memory import create_client_server_memory_streams
from pydantic import ValidationError

from doxagent.agent_mcp.__main__ import build_server
from doxagent.agent_mcp.client import ReadError
from doxagent.agent_mcp.notifications import (
    SEND_URL,
    TOOL_NAME,
    NotificationInput,
    WeChatNotifications,
)
from doxagent.agent_mcp.schemas import TOOLS

MESSAGE = {"title": "巡检报告", "content": "系统正常。\n10 个 ticker 已检查。", "level": "INFO"}
TOKEN = "pushplus-test-fixture-only"


@pytest.mark.parametrize("field", ["token", "channel", "to", "topic", "recipient", "url"])
@pytest.mark.asyncio
async def test_caller_cannot_change_fixed_routing(field):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"code": 200, "data": "receipt"})

    sender = WeChatNotifications(TOKEN, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ValidationError):
            await sender.send({**MESSAGE, field: "other"})
        assert requests == []
    finally:
        await sender.close()


@pytest.mark.parametrize("level", ["INFO", "WARNING", "CRITICAL"])
@pytest.mark.asyncio
async def test_send_uses_only_owner_wechat_plain_text_and_returns_accepted(level):
    requests = []

    def handler(request):
        requests.append(request)
        assert str(request.url) == SEND_URL
        assert request.method == "POST"
        assert json.loads(request.content) == {
            "token": TOKEN,
            "title": f"[{level}] 巡检报告",
            "content": MESSAGE["content"],
            "channel": "wechat",
            "template": "txt",
        }
        return httpx.Response(200, json={"code": 200, "msg": "请求成功", "data": "receipt"})

    sender = WeChatNotifications(TOKEN, transport=httpx.MockTransport(handler))
    try:
        result = await sender.send({**MESSAGE, "level": level})
        assert result["data"]["status"] == "accepted"
        assert result["data"]["delivery"] == "unconfirmed"
        assert result["data"]["short_code"] == "receipt"
        assert TOKEN not in json.dumps(result)
        assert len(requests) == 1
    finally:
        await sender.close()


@pytest.mark.parametrize("change", [
    {"title": " "}, {"content": "\n "}, {"level": "ERROR"},
    {"title": "x" * 101}, {"content": "x" * 10001},
])
def test_empty_or_unbounded_inputs_are_rejected(change):
    with pytest.raises(ValidationError):
        NotificationInput.model_validate({**MESSAGE, **change})


@pytest.mark.parametrize("failure", ["timeout", "http", "json", "shape", "receipt", "provider"])
@pytest.mark.asyncio
async def test_ambiguous_delivery_is_never_retried_or_reported_as_success(failure):
    requests = []

    def handler(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout(TOKEN, request=request)
        if failure == "http":
            return httpx.Response(503, text=TOKEN)
        if failure == "json":
            return httpx.Response(200, text=TOKEN)
        if failure == "shape":
            return httpx.Response(200, json=[])
        if failure == "receipt":
            return httpx.Response(200, json={"code": 200})
        return httpx.Response(200, json={"code": 600, "msg": TOKEN})

    sender = WeChatNotifications(TOKEN, transport=httpx.MockTransport(handler))
    try:
        with pytest.raises(ReadError) as caught:
            await sender.send(MESSAGE)
        assert len(requests) == 1
        assert not caught.value.retryable
        assert TOKEN not in json.dumps(caught.value.wire())
        assert caught.value.code == (
            "PUSHPLUS_REJECTED" if failure == "provider" else "NOTIFICATION_DELIVERY_UNKNOWN"
        )
    finally:
        await sender.close()


@pytest.mark.asyncio
async def test_mcp_advertises_side_effect_and_dispatches_without_business_reads():
    requests, reads = [], []

    class Reads:
        async def call(self, name, arguments):
            reads.append((name, arguments))
            return {"data": {"items": []}, "meta": {}}

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"code": 200, "data": "mcp-receipt"})

    sender = WeChatNotifications(TOKEN, transport=httpx.MockTransport(handler))
    server = build_server(Reads(), sender)
    try:
        async with create_client_server_memory_streams() as (client_io, server_io):
            task = asyncio.create_task(
                server.run(*server_io, server.create_initialization_options())
            )
            try:
                async with ClientSession(*client_io) as client:
                    await client.initialize()
                    listed = {t.name: t for t in (await client.list_tools()).tools}
                    assert set(listed) == {*TOOLS, TOOL_NAME}
                    notification = listed[TOOL_NAME]
                    assert not notification.annotations.read_only_hint
                    assert not notification.annotations.destructive_hint
                    assert not notification.annotations.idempotent_hint
                    assert notification.annotations.open_world_hint
                    assert set(notification.input_schema["properties"]) == {
                        "title", "content", "level"
                    }
                    assert notification.input_schema["additionalProperties"] is False
                    assert all(listed[n].annotations.read_only_hint for n in TOOLS)
                    assert all(listed[n].annotations.idempotent_hint for n in TOOLS)
                    result = await client.call_tool(TOOL_NAME, MESSAGE)
                    assert not result.is_error
                    assert result.structured_content["data"]["short_code"] == "mcp-receipt"
                    assert len(requests) == 1 and not reads
                    invalid = await client.call_tool(TOOL_NAME, {**MESSAGE, "to": "other"})
                    assert invalid.is_error and len(requests) == 1
                    await client.call_tool("doxagent_list_cases", {"ticker": "MU"})
                    assert reads == [("doxagent_list_cases", {"ticker": "MU"})]
            finally:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
    finally:
        await sender.close()
