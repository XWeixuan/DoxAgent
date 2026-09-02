"""Signed stdio MCP exposing only O4 Message Bus and Crawler Plane operations."""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from doxagent.models import AgentName, AgentPermissions
from doxagent.settings import DoxAgentSettings
from doxagent.tools.factory import default_real_tool_registry
from doxagent.tools.schema import ToolRequest
from doxagent.workflows.codex_monitoring_o4.capability import (
    TOOLS_BY_NODE,
    O4OperationCapabilityCodec,
    O4OperationClaims,
)

_OBJECT_FIELDS = {
    "source_parameters",
    "polling",
    "streaming",
    "patch",
    "binding_patches",
    "parameter_schema",
    "default_parameters",
    "default_polling_config",
    "default_streaming_config",
    "scheduler_constraints",
    "parameters",
    "checkpoint_override",
}
_ARRAY_FIELDS = {"entries"}
_BOOLEAN_FIELDS = {
    "enabled",
    "include_disabled",
    "open_only",
    "commit_checkpoint",
    "preserve_response_bodies",
}
_INTEGER_FIELDS = {
    "limit",
    "version",
    "base_version",
    "checkpoint_schema_version",
    "threshold",
    "window",
}
_REQUIRED: dict[str, set[str]] = {
    "monitoring.get_source": {"source_id"},
    "monitoring.update_ticker_config": {"source_id"},
    "monitoring.register_source": {
        "source_id", "display_name", "kind", "adapter_ref", "scheduler_group"
    },
    "monitoring.update_source": {"source_id"},
    "monitoring.hard_delete_source": {"source_id", "reason"},
    "monitoring.update_default_profile": {"profile_id", "entries", "reason"},
    "crawler_plane.get": {"crawler_id"},
    "crawler_plane.create_version": {"crawler_id", "version"},
    "crawler_plane.certify": {"crawler_id", "version"},
    "crawler_plane.promote": {"crawler_id", "version"},
    "crawler_plane.rollback": {"crawler_id", "version"},
    "crawler_plane.execute": {"crawler_id", "binding_id", "source_id", "network_mode"},
    "crawler_plane.live_probe": {"crawler_id", "version"},
    "crawler_plane.get_execution": {"execution_id"},
    "crawler_plane.update_alert_policy": {"crawler_id", "alert_type"},
    "crawler_plane.resolve_alert": {"alert_id"},
    "crawler_plane.register_source": {
        "source_id", "display_name", "crawler_id", "scheduler_group"
    },
    "crawler_plane.add_regression": {"execution_id"},
}


class O4OperationsApplication:
    def __init__(
        self,
        *,
        claims: O4OperationClaims,
        cwd: Path,
        settings: DoxAgentSettings | None = None,
    ) -> None:
        if cwd.resolve().name != claims.run_id:
            raise ValueError("O4 operations MCP cwd does not match signed run_id")
        self.claims = claims
        self.registry = default_real_tool_registry(settings or DoxAgentSettings())
        maximum = TOOLS_BY_NODE[claims.node]
        self.tool_ids = frozenset(claims.enabled_tool_ids).intersection(
            maximum, self.registry.names()
        )
        self.permissions = AgentPermissions(allowed_tools=sorted(self.tool_ids))
        self.by_mcp_name = {tool_id.replace(".", "_"): tool_id for tool_id in self.tool_ids}

    def input_schema(self, tool_id: str) -> dict[str, Any]:
        descriptor = self.registry.describe(tool_id)
        if descriptor is None:
            raise KeyError(tool_id)
        properties: dict[str, Any] = {}
        for field in descriptor.input_fields:
            if field in _OBJECT_FIELDS:
                properties[field] = {"type": "object", "additionalProperties": True}
            elif field in _ARRAY_FIELDS:
                properties[field] = {"type": "array", "items": {"type": "object"}}
            elif field in _BOOLEAN_FIELDS:
                properties[field] = {"type": "boolean"}
            elif field in _INTEGER_FIELDS:
                properties[field] = {"type": "integer"}
            else:
                properties[field] = {"type": "string"}
        return {
            "type": "object",
            "properties": properties,
            "required": sorted(_REQUIRED.get(tool_id, set())),
            "additionalProperties": False,
        }

    def call(self, mcp_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        tool_id = self.by_mcp_name.get(mcp_name)
        if tool_id is None:
            return {"ok": False, "error": {"code": "tool_not_allowed", "message": mcp_name}}
        supplied_ticker = arguments.get("ticker")
        if supplied_ticker and str(supplied_ticker).upper() != self.claims.ticker:
            return {
                "ok": False,
                "error": {
                    "code": "ticker_scope_violation",
                    "message": "tool input ticker differs from signed O4 ticker",
                },
            }
        values = dict(arguments)
        if tool_id in {
            "monitoring.get_ticker_config",
            "monitoring.update_ticker_config",
            "monitoring.list_status",
            "monitoring.recent_events",
            "monitoring.list_failures",
            "crawler_plane.execute",
            "crawler_plane.live_probe",
        }:
            values.setdefault("ticker", self.claims.ticker)
        result = self.registry.call(
            ToolRequest(
                tool_name=tool_id,
                ticker=self.claims.ticker,
                agent_name=AgentName.O4_MARKET_TRACE,
                input=values,
                metadata={
                    "o4_request_id": self.claims.request_id,
                    "o4_node": self.claims.node.value,
                },
            ),
            self.permissions,
        )
        return {
            "ok": result.succeeded,
            "status": result.status.value,
            "summary": result.output_summary,
            "output": result.output,
            "error": result.error.model_dump(mode="json") if result.error else None,
        }


def build_server(application: O4OperationsApplication) -> Server:
    async def list_tools(
        _context: Any, _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        values: list[types.Tool] = []
        for mcp_name, tool_id in sorted(application.by_mcp_name.items()):
            descriptor = application.registry.describe(tool_id)
            if descriptor is None:
                continue
            values.append(
                types.Tool(
                    name=mcp_name,
                    title=tool_id,
                    description=descriptor.description,
                    input_schema=application.input_schema(tool_id),
                    annotations=types.ToolAnnotations(
                        read_only_hint=descriptor.read_only,
                        destructive_hint=tool_id == "monitoring.hard_delete_source",
                        idempotent_hint=descriptor.read_only,
                        open_world_hint=True,
                    ),
                    _meta={"canonical_tool_id": tool_id, "node": application.claims.node.value},
                )
            )
        return types.ListToolsResult(tools=values, cache_scope="private", ttl_ms=0)

    async def call_tool(
        _context: Any, params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        payload = application.call(params.name, dict(params.arguments or {}))
        return types.CallToolResult(
            content=[
                types.TextContent(
                    type="text",
                    text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                )
            ],
            structured_content=payload,
            is_error=not bool(payload.get("ok")),
        )

    return Server(
        "doxagent-o4-operations-mcp",
        version="1.0.0",
        instructions=(
            "Node-scoped O4 control plane. Use these tools for all Message Bus and Crawler "
            "Plane queries and mutations; direct database edits are forbidden."
        ),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def main() -> None:
    token = _required_env("DOXAGENT_O4_OPERATIONS_CAPABILITY")
    public_key = _required_env("DOXAGENT_O4_OPERATIONS_PUBLIC_KEY")
    claims = O4OperationCapabilityCodec.verify(token, public_key=public_key)
    server = build_server(O4OperationsApplication(claims=claims, cwd=Path.cwd()))

    async def run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(run())


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


if __name__ == "__main__":
    main()
