"""Resident, authenticated W3 research MCP mounted in the existing Worker."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from contextvars import ContextVar
from datetime import UTC, datetime

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.responses import JSONResponse

from doxagent.persistent_runtime_v2.w3_research_store import atomic, compact


def service_token(secret):
    return hmac.new(secret.encode(), b"w3-research-http-v2", hashlib.sha256).hexdigest()


class ResearchMcp:
    def __init__(self, store, secret, market=None):
        self.store, self.market = store, market
        self.token = service_token(secret)
        self.ready = {}
        self.calls = 0
        self.capsule = ContextVar("w3_mcp_capsule", default="")
        common = {
            "context_ref": {"type": "string"},
            "cursor": {"type": "string"},
            "max_bytes": {"type": "integer", "minimum": 1024},
        }
        definitions = {
            "catalog": (
                "Discover optional frozen research by business names; no mandatory read.",
                {"kind": {"type": "string"}, "shell": {"type": "string"}},
            ),
            "search": (
                "Find named objects or text in all permitted frozen research, "
                "including other Policies/Shells.",
                {"query": {"type": "string"}, "kind": {"type": "string"}},
            ),
            "read": (
                "Read known objects directly. D2 id is its existing Unit name. "
                "Batch shares one output budget. Follow next_cursor to finish the "
                "selected content; raw_document exposes originals. Repeated tables use "
                "columns/rows; missing lists absent column positions, preserving null values.",
                {
                    "objects": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string"},
                                "id": {"type": "string"},
                                "shell": {"type": "string"},
                                "parts": {
                                    "type": "array",
                                    "items": {"enum": ["state", "baseline", "factors", "gaps"]},
                                },
                            },
                            "required": ["kind", "id"],
                            "additionalProperties": False,
                        },
                    },
                    "view": {"enum": ["business", "sources", "raw"]},
                },
            ),
            "market_view": (
                "Read the prepared 5-day daily/24h hourly/4h five-minute view. "
                "Timeliness/completion only, never direction. PREPARING is nonblocking; "
                "do not busy-poll or call IBKR yourself.",
                {"event_time": {"type": "string"}, "since_version": {"type": "string"}},
            ),
        }
        self.tools = [
            types.Tool(
                name=name,
                description=desc,
                annotations=types.ToolAnnotations(
                    readOnlyHint=True, destructiveHint=False, openWorldHint=False
                ),
                inputSchema={
                    "type": "object",
                    "properties": common | fields,
                    "required": ["context_ref"]
                    + (["objects"] if name == "read" else ["query"] if name == "search" else []),
                    "additionalProperties": False,
                },
            )
            for name, (desc, fields) in definitions.items()
        ]
        self.server = Server(
            "w3_research", version="2", on_list_tools=self.list_tools, on_call_tool=self.call_tool
        )
        self.manager = StreamableHTTPSessionManager(self.server, stateless=True, json_response=True)

    async def list_tools(self, ctx, params):
        if self.capsule.get():
            self.ready[self.capsule.get()] = time.monotonic()
        return types.ListToolsResult(tools=self.tools)

    def _market_read(self, args):
        identity, manifest = self.store.context(args["context_ref"], snapshot_keys=("market_view",))
        if args.get("cursor"):
            cursor = json.loads(base64.urlsafe_b64decode(args["cursor"]))
            resource = cursor.get("resource", "")
            if not re.fullmatch(r"market:[a-f0-9]{64}", resource):
                raise ValueError("invalid market continuation")
            view = json.loads(
                (self.store.root / ".w3-market-evidence" / (resource[7:] + ".json")).read_bytes()
            )
        else:
            view = self.market.view(manifest, args) if self.market else {"status": "UNAVAILABLE"}
        from doxagent.persistent_runtime_v2.w3_research_store import sha

        snapshot = sha(compact(view))
        if not view.get("unchanged"):
            path = self.store.root / ".w3-market-evidence" / (snapshot + ".json")
            if not path.exists():
                atomic(path, compact(view).encode())
        result = self.store.page(
            identity,
            "market:" + snapshot,
            compact(view),
            cursor=args.get("cursor"),
            max_bytes=args.get("max_bytes"),
            reserved_bytes=128,
        )
        result["read_at"] = datetime.now(UTC).isoformat()
        return result

    async def call_tool(self, ctx, params):
        try:
            args = params.arguments or {}
            if params.name == "market_view":
                from doxagent.control_io import read_io

                result = await read_io(self._market_read, args)
            else:
                from doxagent.control_io import read_io

                result = await read_io(self.store.call, params.name, args)
            self.calls += 1
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=compact(result))]
            )
        except (ValueError, KeyError, PermissionError, FileNotFoundError) as exc:
            return types.CallToolResult(
                isError=True,
                content=[
                    types.TextContent(
                        type="text",
                        text=compact(
                            {"error": type(exc).__name__, "message": str(exc), "complete": False}
                        ),
                    )
                ],
            )

    async def __call__(self, scope, receive, send):
        headers = dict(scope.get("headers", []))
        expected = ("Bearer " + self.token).encode()
        if not hmac.compare_digest(headers.get(b"authorization", b""), expected):
            await JSONResponse({"error": "invalid research MCP credential"}, status_code=401)(
                scope, receive, send
            )
            return
        capsule = headers.get(b"x-w3-capsule", b"").decode()
        token = self.capsule.set(capsule)
        try:
            await self.manager.handle_request(scope, receive, send)
        finally:
            self.capsule.reset(token)
