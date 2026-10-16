"""Thin, injectable adapter over the official Codex SDK."""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from openai_codex import ApprovalMode, AsyncCodex, CodexConfig, Sandbox
from openai_codex.types import ReasoningEffort, ReasoningSummary

from doxagent.codex_runtime.models import codex_execution_model
from doxagent.codex_runtime.schema import (
    CodexD2Node,
    CodexD3Node,
    CodexMonitoringO4Node,
    CodexPersistentRuntimeNode,
)
from doxagent.codex_worker.schema import WorkerRunRequest, WorkerTurnTelemetry
from doxagent.codex_worker.telemetry import project_turn_telemetry
from doxagent.codex_worker.turn_usage import read_turn_usage, rollout_path
from doxagent.data_runtime.contracts import build_data_tool_contracts
from doxagent.data_runtime.policy import DataCapabilityCodec, DataToolPolicyRegistry
from doxagent.mcp.data_names import GUIDE_TOOL_NAME, READ_TOOL_NAME
from doxagent.settings import DoxAgentSettings
from doxagent.w3_timing import measured
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
    observation: dict[str, Any] | None = None


class TurnHandle(Protocol):
    async def run(self) -> WorkerTurnResult: ...
    async def interrupt(self) -> None: ...


class CodexExecutionRuntime(Protocol):
    async def start(self, request: WorkerRunRequest, cwd: Path) -> TurnHandle: ...


class _SdkTurnHandle:
    def __init__(
        self,
        *,
        thread_id: str,
        handle: Any,
        audit_path: Path | None = None,
        codex_home: Path | None = None,
        request: WorkerRunRequest | None = None,
        cwd: Path | None = None,
    ) -> None:
        self._thread_id = thread_id
        self.thread_id = thread_id
        self.turn_id = getattr(handle, "id", None)
        self._handle = handle
        self._audit_path = audit_path
        self._codex_home = codex_home
        self._probe_request = request
        self._probe_cwd = cwd
        self._turn_probe = None

    async def _complete_usage(self, telemetry: WorkerTurnTelemetry, turn_id: str) -> None:
        if self._codex_home is None:
            return
        # Native persistence can finish just after the completion notification.
        for attempt in range(3):
            try:
                path = await asyncio.to_thread(rollout_path, self._codex_home, self._thread_id)
                if path is not None:
                    usage, complete = await asyncio.to_thread(
                        read_turn_usage, path, self._thread_id, turn_id
                    )
                    if usage is not None:
                        telemetry.usage = type(telemetry.usage)(**usage)
                        telemetry.observed_usage = usage
                        telemetry.usage_source = "native_turn_receipts_v1"
                        telemetry.usage_complete = complete
                        if complete:
                            return
            except (OSError, ValueError, sqlite3.Error):
                # Accounting must not fail or retry a business turn.
                pass
            if attempt < 2:
                await asyncio.sleep(0.1)

    async def run(self) -> WorkerTurnResult:
        if self._audit_path is not None and hasattr(self._handle, "stream"):
            from doxagent.codex_worker.w3_turn_probe import TurnProbe

            try:
                if self._probe_cwd is not None:
                    self._turn_probe = await asyncio.to_thread(
                        TurnProbe.create,
                        self._probe_cwd,
                        self._probe_request,
                        self.thread_id,
                        self.turn_id,
                    )
            except Exception:
                # Optional observation must never fail a business turn.
                self._turn_probe = None
            probe_status = "interrupted_or_stream_failed"
            observation = None
            try:
                result = await self._stream()
                probe_status = result.status
            finally:
                if self._turn_probe is not None:
                    observation = await asyncio.to_thread(
                        self._turn_probe.finish, self._codex_home, probe_status
                    )
            return replace(result, observation=observation)
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
            await self._complete_usage(telemetry, result.id)
        return WorkerTurnResult(
            thread_id=self._thread_id,
            turn_id=result.id,
            status=status,
            final_response=result.final_response,
            error_message=error_message,
            telemetry=telemetry,
        )

    async def _stream(self) -> WorkerTurnResult:
        from openai_codex.generated.v2_all import (
            AgentMessageThreadItem,
            ItemCompletedNotification,
            MessagePhase,
            ThreadTokenUsageUpdatedNotification,
            TurnCompletedNotification,
        )

        assert self._audit_path is not None
        self._audit_path.parent.mkdir(parents=True, exist_ok=True)
        summary = WorkerTurnTelemetry()
        usage = None
        completed = None
        final = fallback = None
        sequence = 0
        # Retain compact, reasoning-free evidence on disk, not SDK tool payloads in RAM.
        with self._audit_path.open("a", encoding="utf-8") as audit:
            async for notification in self._handle.stream():
                payload = notification.payload
                if self._turn_probe is not None:
                    try:
                        self._turn_probe.observe(payload)
                    except Exception:
                        pass
                if (
                    isinstance(payload, ItemCompletedNotification)
                    and payload.turn_id == self.turn_id
                ):
                    item = payload.item.root
                    if isinstance(item, AgentMessageThreadItem):
                        if item.phase == MessagePhase.final_answer:
                            final = item.text
                        elif item.phase is None:
                            fallback = item.text
                    projected = project_turn_telemetry(
                        items=[payload.item], usage=None, duration_ms=None
                    )
                    for event in projected.events:
                        event.sequence = sequence
                        sequence += 1
                        audit.write(
                            json.dumps(event.model_dump(mode="json"), ensure_ascii=False) + "\n"
                        )
                    audit.flush()
                    for field in (
                        "mcp_call_count",
                        "command_call_count",
                        "subagent_call_count",
                        "file_change_count",
                    ):
                        setattr(summary, field, getattr(summary, field) + getattr(projected, field))
                    summary.events = (summary.events + projected.events)[-256:]
                    summary.failures = (summary.failures + projected.failures)[-64:]
                    summary.slowest_steps = sorted(
                        summary.slowest_steps + projected.slowest_steps,
                        key=lambda e: e.duration_ms or 0,
                        reverse=True,
                    )[:5]
                elif (
                    isinstance(payload, ThreadTokenUsageUpdatedNotification)
                    and payload.turn_id == self.turn_id
                ):
                    usage = payload.token_usage
                elif (
                    isinstance(payload, TurnCompletedNotification)
                    and payload.turn.id == self.turn_id
                ):
                    completed = payload.turn
        if completed is None:
            raise RuntimeError("turn completed event not received")
        projected = project_turn_telemetry(items=(), usage=usage, duration_ms=completed.duration_ms)
        summary.usage = projected.usage
        summary.observed_usage = projected.observed_usage
        summary.sdk_duration_ms = projected.sdk_duration_ms
        await self._complete_usage(summary, completed.id)
        return WorkerTurnResult(
            self.thread_id,
            completed.id,
            completed.status.value,
            final if final is not None else fallback,
            str(completed.error) if completed.error else None,
            summary,
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
        reasoning_summary: str | None = None,
        preserve_requested_model: bool = False,
        sandbox_override: Sandbox | None = None,
    ) -> None:
        self._settings = settings or DoxAgentSettings()
        self._config = config or CodexConfig(client_name="doxagent-codex-worker")
        self._client = AsyncCodex(self._config)
        self._codex_home = Path(
            ((config.env or {}).get("CODEX_HOME") if config else None)
            or os.environ.get("CODEX_HOME")
            or Path.home() / ".codex"
        )
        self.receipt_callback: Any = None
        self._reasoning_summary = reasoning_summary
        self._preserve_requested_model = preserve_requested_model
        self._sandbox_override = sandbox_override

        secret = capability_secret or self._settings.codex_capability_secret
        if not secret:
            raise ValueError("Codex worker Data MCP requires a capability secret")
        self._data_capabilities = DataCapabilityCodec(secret)
        self._o4_capabilities = O4OperationCapabilityCodec(secret)
        self._data_policy = DataToolPolicyRegistry()
        self._data_contracts = None
        self._contract_node = None
        self._prepared_thread = None
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
        account = getattr(account, "root", account)
        models = getattr(model_response, "data", [])
        return {
            "authenticated": account is not None,
            "account_type": getattr(account, "type", None),
            "plan_type": getattr(getattr(account, "plan_type", None), "value", None),
            "models": [getattr(model, "id", None) for model in models],
        }

    async def close(self) -> None:
        await self._client.close()

    async def start(self, request: WorkerRunRequest, cwd: Path) -> TurnHandle:
        return await self._start(request, cwd)

    async def prepare_fork(
        self, request: WorkerRunRequest, cwd: Path, parent_thread_id: str
    ) -> str:
        try:
            return await self._start(request, cwd, prepare_parent=parent_thread_id)
        except BaseException:
            await asyncio.wait_for(self._client.close(), 5)
            self._client = AsyncCodex(self._config)
            self._prepared_thread = None
            raise

    async def _start(self, request: WorkerRunRequest, cwd: Path, *, prepare_parent=None):
        requested_model = request.model
        execution_model = (
            request.model or self._settings.codex_model
            if self._preserve_requested_model
            or (
                request.node == CodexPersistentRuntimeNode.W3
                and request.model in {"gpt-6-sol", "gpt-6-luna"}
            )
            else codex_execution_model(
                request.model or self._settings.codex_model, request.model_provider
            )
        )
        # Resolve at execution, keeping the original request hash and durable
        # idempotency key valid for already frozen or queued requests.
        _write_atomic_text(
            cwd / "attempts" / request.attempt_id / "audit" / "model_selection.json",
            json.dumps(
                {
                    "requested_model": requested_model,
                    "execution_model": execution_model,
                    "provider": request.model_provider,
                    "effort": request.effort,
                },
                indent=2,
            ),
        )
        request = request.model_copy(update={"model": execution_model})
        if request.o4_operations_enabled and not self._container_isolated:
            raise ValueError("O4 operational turns require DOXAGENT_CODEX_CONTAINER_ISOLATION=true")
        # Docker supplies the outer isolation boundary. Nested bubblewrap cannot create
        # a user namespace under the hardened container security profile.
        sandbox = self._sandbox_override or (
            Sandbox.full_access
            if self._container_isolated
            else (Sandbox.read_only if request.read_only else Sandbox.workspace_write)
        )
        control_root = (cwd.parent / ".control" / request.run_id / request.attempt_id).resolve()
        control_root.mkdir(parents=True, exist_ok=True)
        multi_agent_enabled = (
            request.allow_subagents
            and request.max_subagents > 0
            and request.node != CodexD2Node.O1_OPEN_DISCOVERY
        )
        sdk_config: dict[str, Any] = {
            "features.multi_agent": multi_agent_enabled,
            "agents.max_threads": max(1, request.max_subagents),
            "agents.max_depth": 1,
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
            "mcp_servers.source_capture.tools.capture_source.approval_mode": "approve",
            "mcp_servers.source_capture.required": False,
            "mcp_servers.source_capture.enabled": True,
            "mcp_servers.source_capture.startup_timeout_sec": 10,
            "mcp_servers.source_capture.tool_timeout_sec": 30,
        }
        if request.node in {
            CodexD3Node.O3_DISCOVERY,
            CodexD3Node.O3_PLANNING,
            CodexD3Node.O3_BUILD,
            CodexD3Node.O3_INTEGRATION,
            CodexD3Node.O3_MAINTAIN,
        }:
            # Refresh each D3 request, including maintenance and read-only audits.
            # GLOBAL coordinates Planning/Integration; research owners keep their
            # own grants, and an audit must never inherit a preceding grant.
            sdk_config.update(
                {
                    "mcp_servers.data.command": sys.executable,
                    "mcp_servers.data.args": ["-m", "doxagent.mcp.data_server"],
                    "mcp_servers.data.cwd": str(cwd),
                    "mcp_servers.data.required": False,
                    "mcp_servers.data.enabled": request.data_mcp_enabled,
                }
            )

        if isinstance(request.node, CodexD2Node):
            sdk_config.update(
                {
                    "mcp_servers.d2_discovery.command": sys.executable,
                    "mcp_servers.d2_discovery.args": [
                        "-m",
                        "doxagent.workflows.codex_document2.discovery_checkpoint",
                    ],
                    "mcp_servers.d2_discovery.cwd": str(cwd),
                    "mcp_servers.d2_discovery.env.DOXAGENT_CODEX_RUN_ID": request.run_id,
                    "mcp_servers.d2_discovery.env.DOXAGENT_CODEX_ATTEMPT_ID": request.attempt_id,
                    "mcp_servers.d2_discovery.enabled_tools": ["commit_open_discovery_scan"],
                    "mcp_servers.d2_discovery.tools."
                    "commit_open_discovery_scan.approval_mode": "approve",
                    "mcp_servers.d2_discovery.required": False,
                    "mcp_servers.d2_discovery.enabled": (
                        request.node == CodexD2Node.O1_OPEN_DISCOVERY
                    ),
                    "mcp_servers.d2_discovery.startup_timeout_sec": 10,
                    "mcp_servers.d2_discovery.tool_timeout_sec": 30,
                }
            )

        if request.data_mcp_enabled:
            contract_node = "w3" if request.node == CodexPersistentRuntimeNode.W3 else "default"
            if self._contract_node != contract_node:
                if contract_node == "w3":
                    from doxagent.tools.w3_factory import w3_tool_registry as factory
                else:
                    from doxagent.tools.factory import default_real_tool_registry as factory
                self._data_contracts = build_data_tool_contracts(factory(self._settings))
                self._contract_node = contract_node
            sdk_config["mcp_servers.data.enabled"] = True
            allowed_data_tools = self._data_policy.allowed_tools(request.node, request.agent_role)
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
            if request.node == CodexPersistentRuntimeNode.W3:
                scope_file = control_root.parent / "w3-data-scope.json"
                _write_atomic_text(scope_file, json.dumps({"capability": capability}))
                sdk_config["mcp_servers.data.env.DOXAGENT_DATA_MCP_SCOPE_FILE"] = str(scope_file)
            enabled_mcp_tools = [GUIDE_TOOL_NAME, READ_TOOL_NAME]
            enabled_mcp_tools.extend(
                contract.mcp_name
                for tool_id in sorted(allowed_data_tools)
                if (contract := self._data_contracts.get(tool_id)) is not None and contract.exposed
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
                    "mcp_servers.data.env.DOXAGENT_OBSERVATION_CONTROL_ROOT": str(control_root),
                    "mcp_servers.data.env.IBKR_TWS_ENABLED": str(
                        self._settings.ibkr_tws_enabled
                    ).lower(),
                    "mcp_servers.data.env.IBKR_TWS_HOST": self._settings.ibkr_tws_host,
                    "mcp_servers.data.env.IBKR_TWS_PORT": str(self._settings.ibkr_tws_port),
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
                    "mcp_servers.data.required": not isinstance(request.node, CodexD2Node),
                    # Preserve recovered maintenance cold starts and the deployed W3 budget.
                    "mcp_servers.data.startup_timeout_sec": (
                        90
                        if request.node == CodexPersistentRuntimeNode.W3
                        else 60
                        if str(request.node) in {"event_library_o2_maintain", "d3_o3_maintain"}
                        else 20
                    ),
                    "mcp_servers.data.tool_timeout_sec": 120,
                }
            )
        if request.o4_operations_enabled:
            sdk_config["mcp_servers.o4_operations.enabled"] = True
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
        for server in ("data", "source_capture", "o4_operations", "d2_discovery"):
            if (
                request.read_only
                and (
                    not request.data_mcp_enabled
                    or (request.node == CodexPersistentRuntimeNode.W3 and server != "data")
                )
                and f"mcp_servers.{server}.command" in sdk_config
            ):
                sdk_config[f"mcp_servers.{server}.enabled"] = False
            if f"mcp_servers.{server}.command" in sdk_config:
                for name in ("DOXAGENT_CAPSULE_ID", "DOXAGENT_MCP_BUDGET_ROOT"):
                    if os.environ.get(name):
                        sdk_config[f"mcp_servers.{server}.env.{name}"] = os.environ[name]
        if request.web_search_mode is not None:
            sdk_config["web_search"] = request.web_search_mode
        elif request.read_only and not request.data_mcp_enabled:
            sdk_config["web_search"] = "disabled"
        scope = (
            str(cwd),
            request.attempt_id,
            request.cutoff_at.isoformat(),
            request.model,
            request.model_provider,
            request.effort,
            request.data_mcp_enabled,
            request.web_search_mode,
        )
        if prepare_parent is not None:
            thread = await measured(
                "sdk.thread_fork",
                asyncio.wait_for(
                    self._client.thread_fork(
                        prepare_parent,
                        cwd=str(cwd),
                        model=request.model,
                        model_provider=request.model_provider,
                        sandbox=sandbox,
                        approval_mode=ApprovalMode.deny_all,
                        config=sdk_config,
                    ),
                    10,
                ),
            )
            self._prepared_thread = (thread, scope, sdk_config)
            return thread.id
        prepared = self._prepared_thread
        if prepared and prepared[0].id == request.thread_id and prepared[1] == scope:
            thread, _, sdk_config = prepared
            self._prepared_thread = None
        elif request.thread_id:
            thread = await measured(
                "sdk.thread_resume",
                self._client.thread_resume(
                    request.thread_id,
                    cwd=str(cwd),
                    model=request.model,
                    model_provider=request.model_provider,
                    sandbox=sandbox,
                    approval_mode=ApprovalMode.deny_all,
                    config=sdk_config,
                ),
            )
        else:
            thread = await measured(
                "sdk.thread_start",
                self._client.thread_start(
                    cwd=str(cwd),
                    model=request.model,
                    model_provider=request.model_provider,
                    sandbox=sandbox,
                    approval_mode=ApprovalMode.deny_all,
                    config=sdk_config,
                    base_instructions=(
                        (
                            "Work inside the current run workspace. For O4 operational "
                            "turns, direct "
                            "file writes are additionally allowed only below "
                            f"{self._settings.crawler_plane_root}/working; never write releases or "
                            "control-plane databases directly. "
                            if request.o4_operations_enabled
                            else "Work only inside the current run workspace and preserve "
                            "workspace "
                            "audit boundaries. "
                        )
                        + "Read the attempt-local AGENTS.md and task.json named in the turn before "
                        "acting, then follow their file paths. Use configured MCP tools only "
                        "within "
                        "their granted capability. "
                        f"Never spawn more than {request.max_subagents} subagents."
                    ),
                ),
            )
        if self.receipt_callback is not None:
            self.receipt_callback(thread.id, None)
        handle = await measured(
            "sdk.turn_start",
            thread.turn(
                request.prompt,
                cwd=str(cwd),
                effort=ReasoningEffort(request.effort),
                model=request.model,
                sandbox=sandbox,
                approval_mode=ApprovalMode.deny_all,
                # The Codex structured-output API requires a top-level object.
                # Markdown-only C4 network-build keeps its string contract in the
                # workspace and validates the returned text in NodeRunner instead.
                **(
                    {"output_schema": request.output_schema}
                    if request.output_schema.get("type") == "object"
                    else {}
                ),
                **(
                    {"summary": ReasoningSummary(self._reasoning_summary)}
                    if self._reasoning_summary
                    else {}
                ),
            ),
        )
        await asyncio.sleep(0)
        if self.receipt_callback is not None:
            self.receipt_callback(thread.id, getattr(handle, "id", None))
        return _SdkTurnHandle(
            thread_id=thread.id,
            handle=handle,
            audit_path=cwd / "attempts" / request.attempt_id / "audit" / "sdk_loop.jsonl",
            codex_home=self._codex_home,
            request=request,
            cwd=cwd,
        )


def _write_atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()
