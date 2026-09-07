"""Signed stdio MCP exposing only O4 Message Bus and Crawler Plane operations."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from doxagent.codex_runtime.errors import CapabilityDenied
from doxagent.models import AgentName, AgentPermissions
from doxagent.settings import DoxAgentSettings
from doxagent.tools.factory import default_real_tool_registry
from doxagent.tools.schema import ToolRequest
from doxagent.workflows.codex_monitoring_o4.capability import (
    TOOLS_BY_NODE,
    O4OperationCapabilityCodec,
    O4OperationClaims,
)
from doxagent.workflows.codex_monitoring_o4.policy import (
    O4CrawlerPromotionPolicy,
    O4MutationPolicy,
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
        "source_id",
        "display_name",
        "kind",
        "adapter_ref",
        "scheduler_group",
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
    "crawler_plane.resolve_retry": {"retry_id"},
    "crawler_plane.reactivate_retry": {"retry_id"},
    "crawler_plane.register_source": {"source_id", "display_name", "crawler_id", "scheduler_group"},
    "crawler_plane.add_regression": {"execution_id"},
}


class O4OperationsApplication:
    def __init__(
        self,
        *,
        claims: O4OperationClaims,
        cwd: Path,
        settings: DoxAgentSettings | None = None,
        capability_loader: Callable[[], O4OperationClaims] | None = None,
    ) -> None:
        if cwd.resolve().name != claims.run_id:
            raise ValueError("O4 operations MCP cwd does not match signed run_id")
        self._initial_claims = claims
        self._capability_loader = capability_loader
        self._cwd = cwd.resolve()
        self._settings = settings or DoxAgentSettings()
        self._configuration_scope = claims.initialization_id
        if claims.initialization_id:
            from doxagent.ticker_initialization.configuration import candidate_bus_path

            self._settings = self._settings.model_copy(
                update={
                    "message_bus_v2_sqlite_path": str(
                        candidate_bus_path(
                            self._settings.message_bus_v2_sqlite_path, claims.initialization_id
                        )
                    ),
                }
            )
        self.registry = default_real_tool_registry(self._settings)
        self._mutation_policy = O4MutationPolicy(
            standard_poll_seconds=self._settings.o4_standard_poll_seconds,
            tikhub_poll_seconds=self._settings.o4_tikhub_poll_seconds,
            alert_after_seconds=self._settings.o4_alert_after_seconds,
        )

    @property
    def claims(self) -> O4OperationClaims:
        return self._snapshot()[0]

    @property
    def tool_ids(self) -> frozenset[str]:
        return self._snapshot()[1]

    @property
    def permissions(self) -> AgentPermissions:
        return self._snapshot()[2]

    @property
    def by_mcp_name(self) -> dict[str, str]:
        return self._snapshot()[3]

    def _snapshot(
        self,
    ) -> tuple[
        O4OperationClaims,
        frozenset[str],
        AgentPermissions,
        dict[str, str],
    ]:
        claims = (
            self._capability_loader()
            if self._capability_loader is not None
            else self._initial_claims
        )
        if self._cwd.name != claims.run_id:
            raise CapabilityDenied("O4 operations MCP run scope changed")
        if claims.initialization_id != self._configuration_scope:
            raise CapabilityDenied(
                "O4 candidate configuration scope changed; reopen the MCP session"
            )
        maximum = TOOLS_BY_NODE[claims.node]
        tool_ids = frozenset(claims.enabled_tool_ids).intersection(maximum, self.registry.names())
        permissions = AgentPermissions(allowed_tools=sorted(tool_ids))
        by_mcp_name = {tool_id.replace(".", "_"): tool_id for tool_id in tool_ids}
        return claims, tool_ids, permissions, by_mcp_name

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
        try:
            claims, _tool_ids, permissions, by_mcp_name = self._snapshot()
        except CapabilityDenied as exc:
            return {
                "ok": False,
                "error": {"code": "capability_denied", "message": str(exc)},
            }
        tool_id = by_mcp_name.get(mcp_name)
        if tool_id is None:
            return {"ok": False, "error": {"code": "tool_not_allowed", "message": mcp_name}}
        supplied_ticker = arguments.get("ticker")
        if supplied_ticker and str(supplied_ticker).upper() != claims.ticker:
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
            values.setdefault("ticker", claims.ticker)
        try:
            values = self._apply_o4_policy(
                tool_id,
                values,
                claims=claims,
                permissions=permissions,
            )
        except (KeyError, OSError, TypeError, ValueError) as exc:
            return {
                "ok": False,
                "error": {
                    "code": "o4_mutation_policy_denied",
                    "message": str(exc),
                },
            }
        result = self.registry.call(
            ToolRequest(
                tool_name=tool_id,
                ticker=claims.ticker,
                agent_name=AgentName.O4_MARKET_TRACE,
                input=values,
                metadata={
                    "o4_request_id": claims.request_id,
                    "o4_node": claims.node.value,
                },
            ),
            permissions,
        )
        return {
            "ok": result.succeeded,
            "status": result.status.value,
            "summary": result.output_summary,
            "output": result.output,
            "error": result.error.model_dump(mode="json") if result.error else None,
        }

    def _apply_o4_policy(
        self,
        tool_id: str,
        values: dict[str, Any],
        *,
        claims: O4OperationClaims,
        permissions: AgentPermissions,
    ) -> dict[str, Any]:
        if tool_id == "monitoring.register_source":
            return self._mutation_policy.canonicalize_source_registration(values)
        if tool_id == "monitoring.update_source":
            source = self._source(str(values.get("source_id", "")), claims, permissions)
            return self._mutation_policy.canonicalize_source_update(values, current_source=source)
        if tool_id == "crawler_plane.register_source":
            output = dict(values)
            output["default_polling_config"] = self._mutation_policy.polling(
                "crawler", output.get("default_polling_config")
            )
            return output
        if tool_id == "monitoring.update_default_profile":
            return self._mutation_policy.canonicalize_profile(
                values,
                source_loader=lambda source_id: self._source(source_id, claims, permissions),
            )
        if tool_id == "monitoring.update_ticker_config":
            source_id = str(values.get("source_id", "")).strip().lower()
            source = self._source(source_id, claims, permissions)
            config = self._internal_output(
                "monitoring.get_ticker_config",
                {"ticker": claims.ticker},
                claims,
                permissions,
            )
            raw_bindings = config.get("bindings", [])
            bindings = [dict(item) for item in raw_bindings if isinstance(item, dict)]
            existing = next((item for item in bindings if item.get("source_id") == source_id), None)
            output = self._mutation_policy.canonicalize_binding(
                values,
                source=source,
                existing_binding=existing,
            )
            if existing is None:
                projected = {
                    "source_id": source_id,
                    "enabled": bool(output.get("enabled", True)),
                    "source_parameters": output.get(
                        "source_parameters", source.get("default_parameters", {})
                    ),
                    "polling": output["polling"],
                }
                bindings.append(projected)
            else:
                replacement = dict(existing)
                replacement.update(
                    {
                        key: output[key]
                        for key in ("enabled", "source_parameters", "polling")
                        if key in output
                    }
                )
                bindings = [
                    replacement if item.get("source_id") == source_id else item for item in bindings
                ]
            self._mutation_policy.validate_ticker_account_cap(
                bindings=bindings,
                source_loader=lambda item_source_id: self._source(
                    item_source_id, claims, permissions
                ),
            )
            return output
        if tool_id == "crawler_plane.promote":
            crawler_id = str(values.get("crawler_id", "")).strip().lower()
            version = int(values.get("version", 0))
            detail = self._internal_output(
                "crawler_plane.get",
                {"crawler_id": crawler_id},
                claims,
                permissions,
            )
            versions = detail.get("versions", [])
            candidate = next(
                (
                    item
                    for item in versions
                    if isinstance(item, dict)
                    and int(item.get("spec", {}).get("version", 0)) == version
                ),
                None,
            )
            if candidate is None or not candidate.get("working_path"):
                raise ValueError(f"crawler working version not found: {crawler_id}@{version}")
            O4CrawlerPromotionPolicy.validate(str(candidate["working_path"]))
        return values

    def _source(
        self,
        source_id: str,
        claims: O4OperationClaims,
        permissions: AgentPermissions,
    ) -> dict[str, Any]:
        output = self._internal_output(
            "monitoring.get_source",
            {"source_id": source_id},
            claims,
            permissions,
        )
        source = output.get("source")
        if not isinstance(source, dict):
            raise ValueError(f"source lookup returned no SourceDefinition: {source_id}")
        return source

    def _internal_output(
        self,
        tool_id: str,
        values: dict[str, Any],
        claims: O4OperationClaims,
        permissions: AgentPermissions,
    ) -> dict[str, Any]:
        result = self.registry.call(
            ToolRequest(
                tool_name=tool_id,
                ticker=claims.ticker,
                agent_name=AgentName.O4_MARKET_TRACE,
                input=values,
                metadata={
                    "o4_request_id": claims.request_id,
                    "o4_node": claims.node.value,
                    "o4_policy_read": True,
                },
            ),
            permissions,
        )
        if not result.succeeded:
            message = result.error.message if result.error else result.output_summary
            raise ValueError(message)
        if not isinstance(result.output, dict):
            raise ValueError(f"O4 policy read returned invalid output for {tool_id}")
        return result.output


def build_server(application: O4OperationsApplication) -> Server:
    async def list_tools(
        _context: Any, _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        values: list[types.Tool] = []
        try:
            claims, _tool_ids, _permissions, by_mcp_name = application._snapshot()
        except CapabilityDenied:
            # An expired/revoked capability must fail closed.  Returning an
            # empty list also lets a Codex client refresh after the controller
            # atomically replaces the capability file.
            return types.ListToolsResult(tools=[], cache_scope="private", ttl_ms=0)
        for mcp_name, tool_id in sorted(by_mcp_name.items()):
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
                    _meta={"canonical_tool_id": tool_id, "node": claims.node.value},
                )
            )
        return types.ListToolsResult(tools=values, cache_scope="private", ttl_ms=0)

    async def call_tool(_context: Any, params: types.CallToolRequestParams) -> types.CallToolResult:
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
    cwd = Path.cwd().resolve()
    public_key = _required_env("DOXAGENT_O4_OPERATIONS_PUBLIC_KEY")
    capability_file = os.environ.get("DOXAGENT_O4_OPERATIONS_CAPABILITY_FILE")
    if capability_file:
        capability_path = _resolve_capability_path(capability_file, cwd=cwd)

        def load_capability() -> O4OperationClaims:
            try:
                token = capability_path.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise CapabilityDenied("O4 operation capability file is unavailable") from exc
            if not token:
                raise CapabilityDenied("O4 operation capability file is empty")
            return O4OperationCapabilityCodec.verify(token, public_key=public_key)

        claims = load_capability()
        application = O4OperationsApplication(
            claims=claims,
            cwd=cwd,
            capability_loader=load_capability,
        )
    else:
        token = _required_env("DOXAGENT_O4_OPERATIONS_CAPABILITY")
        claims = O4OperationCapabilityCodec.verify(token, public_key=public_key)
        application = O4OperationsApplication(claims=claims, cwd=cwd)
    server = build_server(application)

    async def run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(read_stream, write_stream, server.create_initialization_options())

    asyncio.run(run())


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _resolve_capability_path(value: str, *, cwd: Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = cwd / path
    resolved = path.resolve()
    allowed_roots = (cwd, cwd.parent / ".control")
    if not any(_is_relative_to(resolved, root.resolve()) for root in allowed_roots):
        raise RuntimeError("DOXAGENT_O4_OPERATIONS_CAPABILITY_FILE must stay in the run scope")
    return resolved


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


if __name__ == "__main__":
    main()
