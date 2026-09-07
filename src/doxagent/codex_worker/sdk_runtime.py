"""Thin, injectable adapter over the official Codex SDK."""

from __future__ import annotations

import asyncio
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
from openai_codex.types import ReasoningEffort

from doxagent.codex_runtime.schema import CodexMonitoringO4Node
from doxagent.codex_worker.schema import WorkerRunRequest, WorkerTurnTelemetry
from doxagent.codex_worker.telemetry import project_turn_telemetry
from doxagent.data_runtime.contracts import build_data_tool_contracts
from doxagent.data_runtime.policy import DataCapabilityCodec, DataToolPolicyRegistry
from doxagent.mcp.data_server import GUIDE_TOOL_NAME, READ_TOOL_NAME
from doxagent.settings import DoxAgentSettings
from doxagent.tools.factory import default_real_tool_registry
from doxagent.workflows.codex_monitoring_o4.capability import (
    ALL_O4_TOOLS,
    TOOLS_BY_NODE,
    O4OperationCapabilityCodec,
)


@dataclass(frozen=True)
class WorkerTurnResult:
    thread_id: str
    turn_id: str
    status: str
    final_response: str | None
    error_message: str | None = None
    telemetry: WorkerTurnTelemetry | None = None


class TurnHandle(Protocol):
    async def run(self) -> WorkerTurnResult: ...
    async def interrupt(self) -> None: ...


class CodexExecutionRuntime(Protocol):
    async def start(self, request: WorkerRunRequest, cwd: Path) -> TurnHandle: ...


class _SdkTurnHandle:
    def __init__(self, *, thread_id: str, handle: Any) -> None:
        self._thread_id = thread_id
        self._handle = handle

    async def run(self) -> WorkerTurnResult:
        result = await self._handle.run()
        error = getattr(result, "error", None)
        error_message = str(error) if error is not None else None
        status = getattr(result.status, "value", str(result.status))
        telemetry = None
        if any(hasattr(result, name) for name in ("items", "usage", "duration_ms")):
            telemetry = project_turn_telemetry(
                items=getattr(result, "items", ()),
                usage=getattr(result, "usage", None),
                duration_ms=getattr(result, "duration_ms", None),
            )
        return WorkerTurnResult(
            thread_id=self._thread_id,
            turn_id=result.id,
            status=status,
            final_response=result.final_response,
            error_message=error_message,
            telemetry=telemetry,
        )

    async def interrupt(self) -> None:
        await self._handle.interrupt()


class OpenAICodexRuntime:
    """Runs one turn in a run-scoped cwd with approvals disabled."""

    def __init__(
        self,
        *,
        config: CodexConfig | None = None,
        capability_secret: str | None = None,
        container_isolated: bool | None = None,
        settings: DoxAgentSettings | None = None,
    ) -> None:
        self._settings = settings or DoxAgentSettings()
        self._client = AsyncCodex(config or CodexConfig(client_name="doxagent-codex-worker"))
        secret = capability_secret or self._settings.codex_capability_secret
        if not secret:
            raise ValueError("Codex worker Data MCP requires a capability secret")
        self._data_capabilities = DataCapabilityCodec(secret)
        self._o4_capabilities = O4OperationCapabilityCodec(secret)
        self._data_policy = DataToolPolicyRegistry()
        self._data_contracts = build_data_tool_contracts(default_real_tool_registry(self._settings))
        self._container_isolated = (
            container_isolated
            if container_isolated is not None
            else os.environ.get("DOXAGENT_CODEX_CONTAINER_ISOLATION", "").lower() == "true"
        )

    async def probe(self) -> dict[str, object]:
        """Read account/model readiness without returning identity or secrets."""

        account_response = await self._client.account()
        model_response = await self._client.models(include_hidden=False)
        account = getattr(account_response, "account", None)
        models = getattr(model_response, "data", [])
        return {
            "authenticated": account is not None,
            "account_type": getattr(account, "type", None),
            "plan_type": getattr(getattr(account, "plan_type", None), "value", None),
            "models": [getattr(model, "id", None) for model in models],
        }

    async def start(self, request: WorkerRunRequest, cwd: Path) -> TurnHandle:
        if request.o4_operations_enabled and not self._container_isolated:
            raise ValueError(
                "O4 operational turns require DOXAGENT_CODEX_CONTAINER_ISOLATION=true"
            )
        # Docker supplies the outer isolation boundary. Nested bubblewrap cannot create
        # a user namespace under the hardened container security profile.
        sandbox = (
            Sandbox.full_access
            if self._container_isolated
            else (Sandbox.read_only if request.read_only else Sandbox.workspace_write)
        )
        control_root = (cwd.parent / ".control" / request.run_id / request.attempt_id).resolve()
        control_root.mkdir(parents=True, exist_ok=True)
        multi_agent_enabled = request.allow_subagents and request.max_subagents > 0
        sdk_config: dict[str, Any] = {
            "features.multi_agent": multi_agent_enabled,
            "web_search": "live",
            "mcp_servers.source_capture.command": sys.executable,
            "mcp_servers.source_capture.args": [
                "-m",
                "doxagent.mcp.source_capture_server",
            ],
            "mcp_servers.source_capture.cwd": str(cwd),
            "mcp_servers.source_capture.env.DOXAGENT_CODEX_RUN_ID": request.run_id,
            "mcp_servers.source_capture.env.DOXAGENT_CODEX_ATTEMPT_ID": request.attempt_id,
            "mcp_servers.source_capture.env.DOXAGENT_OBSERVATION_CONTROL_ROOT": str(control_root),
            "mcp_servers.source_capture.enabled_tools": ["capture_source"],
            "mcp_servers.source_capture.required": False,
            "mcp_servers.source_capture.startup_timeout_sec": 10,
            "mcp_servers.source_capture.tool_timeout_sec": 30,
        }
        if request.data_mcp_enabled:
            allowed_data_tools = self._data_policy.allowed_tools(
                request.node, request.agent_role
            )
            capability = self._data_capabilities.issue(
                workflow_version=request.workflow_version,
                research_lane=request.research_lane,
                run_id=request.run_id,
                node_id=request.node,
                node_attempt_id=request.attempt_id,
                agent_role=request.agent_role,
                ticker=request.ticker,
                cutoff_at=request.cutoff_at,
                enabled_tool_ids=allowed_data_tools,
                ttl_seconds=request.timeout_seconds + 300,
            )
            enabled_mcp_tools = [GUIDE_TOOL_NAME, READ_TOOL_NAME]
            enabled_mcp_tools.extend(
                contract.mcp_name
                for tool_id in sorted(allowed_data_tools)
                if (contract := self._data_contracts.get(tool_id)) is not None
                and contract.exposed
            )
            sdk_config.update(
                {
                    "mcp_servers.data.command": sys.executable,
                    "mcp_servers.data.args": ["-m", "doxagent.mcp.data_server"],
                    "mcp_servers.data.cwd": str(cwd),
                    "mcp_servers.data.env.DOXAGENT_DATA_MCP_CAPABILITY": capability,
                    "mcp_servers.data.env.DOXAGENT_DATA_MCP_PUBLIC_KEY": (
                        self._data_capabilities.public_key
                    ),
                    "mcp_servers.data.env.DOXAGENT_OBSERVATION_CONTROL_ROOT": str(
                        control_root
                    ),
                    "mcp_servers.data.env.IBKR_TWS_ENABLED": str(
                        self._settings.ibkr_tws_enabled
                    ).lower(),
                    "mcp_servers.data.env.IBKR_TWS_HOST": self._settings.ibkr_tws_host,
                    "mcp_servers.data.env.IBKR_TWS_PORT": str(
                        self._settings.ibkr_tws_port
                    ),
                    "mcp_servers.data.env.IBKR_TWS_CLIENT_ID": str(
                        self._settings.ibkr_tws_client_id
                    ),
                    "mcp_servers.data.env.IBKR_TWS_TIMEOUT_SECONDS": str(
                        self._settings.ibkr_tws_timeout_seconds
                    ),
                    "mcp_servers.data.env.IBKR_TWS_MARKET_DATA_TYPE": str(
                        self._settings.ibkr_tws_market_data_type
                    ),
                    "mcp_servers.data.enabled_tools": enabled_mcp_tools,
                    "mcp_servers.data.required": True,
                    "mcp_servers.data.startup_timeout_sec": 20,
                    "mcp_servers.data.tool_timeout_sec": 120,
                }
            )
        if request.o4_operations_enabled:
            if not isinstance(request.node, CodexMonitoringO4Node):
                raise ValueError("O4 operations are available only to O4 workflow nodes")
            allowed_o4_tools = TOOLS_BY_NODE.get(request.node)
            if allowed_o4_tools is None:
                raise ValueError("O4 operations are available only to O4 workflow nodes")
            capability = self._o4_capabilities.issue(
                run_id=request.run_id,
                request_id=request.attempt_id,
                ticker=request.ticker,
                node=request.node,
                enabled_tool_ids=allowed_o4_tools,
                initialization_id=request.initialization_id,
                ttl_seconds=request.timeout_seconds + 300,
            )
            # Keep one controller-owned capability file for the ticker thread.
            # A persistent Codex thread may keep its MCP subprocess alive when
            # thread_resume changes config; the server rereads this file on
            # every list/call and therefore observes the new node capability.
            o4_capability_file = control_root.parent / "o4_operations_capability.token"
            _write_atomic_text(o4_capability_file, capability)
            sdk_config.update(
                {
                    "mcp_servers.o4_operations.command": sys.executable,
                    "mcp_servers.o4_operations.args": [
                        "-m",
                        "doxagent.mcp.o4_operations_server",
                    ],
                    "mcp_servers.o4_operations.cwd": str(cwd),
                    "mcp_servers.o4_operations.env.DOXAGENT_O4_OPERATIONS_CAPABILITY": capability,
                    "mcp_servers.o4_operations.env.DOXAGENT_O4_OPERATIONS_CAPABILITY_FILE": str(
                        o4_capability_file
                    ),
                    "mcp_servers.o4_operations.env.DOXAGENT_O4_OPERATIONS_PUBLIC_KEY": (
                        self._o4_capabilities.public_key
                    ),
                    "mcp_servers.o4_operations.env.DOXAGENT_MESSAGE_BUS_V2_ENABLED": str(
                        self._settings.message_bus_v2_enabled
                    ).lower(),
                    "mcp_servers.o4_operations.env.DOXAGENT_MESSAGE_BUS_V2_SQLITE_PATH": (
                        self._settings.message_bus_v2_sqlite_path
                    ),
                    "mcp_servers.o4_operations.env.DOXAGENT_MESSAGE_BUS_V2_ADAPTER_ROOT": (
                        self._settings.message_bus_v2_adapter_root
                    ),
                    "mcp_servers.o4_operations.env.DOXAGENT_CRAWLER_PLANE_ROOT": (
                        self._settings.crawler_plane_root
                    ),
                    "mcp_servers.o4_operations.env.DOXAGENT_CRAWLER_PLANE_SQLITE_PATH": (
                        self._settings.crawler_plane_sqlite_path
                    ),
                    "mcp_servers.o4_operations.enabled_tools": [
                        tool_id.replace(".", "_") for tool_id in sorted(ALL_O4_TOOLS)
                    ],
                    "mcp_servers.o4_operations.required": True,
                    "mcp_servers.o4_operations.startup_timeout_sec": 20,
                    "mcp_servers.o4_operations.tool_timeout_sec": 7_200,
                }
            )
        if request.thread_id:
            thread = await self._client.thread_resume(
                request.thread_id,
                cwd=str(cwd),
                model=request.model,
                model_provider=request.model_provider,
                sandbox=sandbox,
                approval_mode=ApprovalMode.deny_all,
                config=sdk_config,
            )
        else:
            thread = await self._client.thread_start(
                cwd=str(cwd),
                model=request.model,
                model_provider=request.model_provider,
                sandbox=sandbox,
                approval_mode=ApprovalMode.deny_all,
                config=sdk_config,
                base_instructions=(
                    (
                        "Work inside the current run workspace. For O4 operational turns, direct "
                        "file writes are additionally allowed only below "
                        f"{self._settings.crawler_plane_root}/working; never write releases or "
                        "control-plane databases directly. "
                        if request.o4_operations_enabled
                        else "Work only inside the current run workspace and preserve workspace "
                        "audit boundaries. "
                    )
                    + "Read the attempt-local AGENTS.md and task.json named in the turn before "
                    "acting, then follow their file paths. Use configured MCP tools only within "
                    "their granted capability. "
                    f"Never spawn more than {request.max_subagents} subagents."
                ),
            )
        handle = await thread.turn(
            request.prompt,
            cwd=str(cwd),
            effort=ReasoningEffort(request.effort),
            model=request.model,
            output_schema=request.output_schema,
            sandbox=sandbox,
            approval_mode=ApprovalMode.deny_all,
        )
        await asyncio.sleep(0)
        return _SdkTurnHandle(thread_id=thread.id, handle=handle)


def _write_atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
