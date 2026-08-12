"""Attempt-scoped low-level MCP 2.0 stdio server for semantic data tools."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from doxagent.data_runtime.contracts import (
    DataExecutionContext,
    DataToolContract,
    build_data_tool_contracts,
)
from doxagent.data_runtime.execution import DataExecutionCore
from doxagent.data_runtime.guidance import DataToolGuide
from doxagent.data_runtime.pilot_case import validate_pilot_case_root
from doxagent.data_runtime.policy import (
    DataCapabilityClaims,
    DataCapabilityCodec,
    DataToolPolicyRegistry,
)
from doxagent.observations.kernel import ObservationKernel
from doxagent.observations.store import AttemptObservationStore
from doxagent.settings import DoxAgentSettings
from doxagent.tools.factory import default_real_tool_registry

GUIDE_TOOL_NAME = "data_tool_guide"
READ_TOOL_NAME = "data_read_observation"


class DataMcpApplication:
    def __init__(
        self,
        *,
        claims: DataCapabilityClaims,
        run_root: Path,
        control_root: Path,
        settings: DoxAgentSettings | None = None,
    ) -> None:
        resolved_run_root = run_root.resolve()
        if resolved_run_root.name == claims.run_id:
            expected_control_root = (
                resolved_run_root.parent / ".control" / claims.run_id / claims.node_attempt_id
            ).resolve()
        elif claims.pilot_case_id:
            validate_pilot_case_root(
                run_root=resolved_run_root,
                pilot_case_id=claims.pilot_case_id,
                run_id=claims.run_id,
                attempt_id=claims.node_attempt_id,
                node=claims.node_id,
            )
            expected_control_root = (
                resolved_run_root / ".control" / claims.run_id / claims.node_attempt_id
            ).resolve()
        else:
            raise ValueError("Data MCP cwd does not match capability run_id")
        if control_root.resolve() != expected_control_root:
            raise ValueError("Data MCP control root does not match attempt scope")
        tools = default_real_tool_registry(settings or DoxAgentSettings())
        contracts = build_data_tool_contracts(tools)
        policy = DataToolPolicyRegistry()
        effective = policy.effective_tools(claims).intersection(tools.names())
        self.claims = claims
        self.contracts = contracts
        self.effective_tool_ids = frozenset(effective)
        mirror_root = (
            run_root
            / "attempts"
            / claims.node_attempt_id
            / "audit"
            / "observations"
        )
        store = AttemptObservationStore(
            control_root=expected_control_root,
            mirror_root=mirror_root,
            run_id=claims.run_id,
            attempt_id=claims.node_attempt_id,
        )
        self.observations = ObservationKernel(store=store, run_root=run_root)
        self.execution = DataExecutionCore(
            tools=tools,
            contracts=contracts,
            context=DataExecutionContext(
                run_id=claims.run_id,
                node_id=claims.node_id,
                node_attempt_id=claims.node_attempt_id,
                agent_role=claims.agent_role,
                ticker=claims.ticker,
                cutoff_at=claims.cutoff_at,
                enabled_tool_ids=frozenset(effective),
            ),
            observations=self.observations,
        )
        self.guide = DataToolGuide(contracts)
        self.catalog_path = self._write_catalog(resolved_run_root)

    def exposed_contracts(self) -> list[DataToolContract]:
        return [
            contract
            for contract in self.contracts.all()
            if contract.canonical_tool_id in self.effective_tool_ids and contract.exposed
        ]

    def instructions(self) -> str:
        return (
            self.guide.server_instructions(self.effective_tool_ids)
            + f" Authorized catalog: {self.catalog_path}."
        )

    def _write_catalog(self, run_root: Path) -> str:
        relative = Path("context") / "data_tool_catalog" / f"{self.claims.node_attempt_id}.md"
        target = run_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        content = self.guide.render_catalog(self.effective_tool_ids)
        if target.exists():
            if target.read_text(encoding="utf-8") != content:
                raise ValueError("immutable Data MCP catalog changed for the same attempt")
            return relative.as_posix()
        descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            try:
                target.chmod(0o444)
            except OSError:
                pass
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return relative.as_posix()


def build_server(application: DataMcpApplication) -> Server:
    async def list_tools(
        _context: Any,
        _params: types.PaginatedRequestParams | None,
    ) -> types.ListToolsResult:
        tools = [
            types.Tool(
                name=GUIDE_TOOL_NAME,
                description=(
                    "Deterministically recommend authorized semantic data tools for a business "
                    "question. This performs no provider or model request."
                ),
                input_schema={
                    "type": "object",
                    "properties": {
                        "task": {"type": "string", "minLength": 1, "maxLength": 4_000},
                        "business_category": {"type": "string", "maxLength": 100},
                        "as_of": {"type": "string", "maxLength": 100},
                    },
                    "required": ["task"],
                    "additionalProperties": False,
                },
                annotations=_read_annotations(idempotent=True),
            ),
            types.Tool(
                name=READ_TOOL_NAME,
                description=(
                    "Read one complete cleaned attempt-local O# block. Prefer native Observation "
                    "Pack files for large results; use this as a bounded compatibility fallback."
                ),
                input_schema={
                    "type": "object",
                    "properties": {"alias": {"type": "string", "pattern": "^O[1-9]\\d*$"}},
                    "required": ["alias"],
                    "additionalProperties": False,
                },
                annotations=_read_annotations(idempotent=True),
            ),
        ]
        for contract in application.exposed_contracts():
            tools.append(
                types.Tool(
                    name=contract.mcp_name,
                    title=contract.canonical_tool_id,
                    description=contract.mcp_description(),
                    input_schema=contract.input_schema,
                    annotations=_read_annotations(idempotent=False),
                    _meta={
                        "canonical_tool_id": contract.canonical_tool_id,
                        "source_name": contract.source_name,
                        "business_categories": contract.business_categories,
                        "contract_version": contract.contract_version,
                    },
                )
            )
        return types.ListToolsResult(tools=tools, cache_scope="private", ttl_ms=0)

    async def call_tool(
        _context: Any,
        params: types.CallToolRequestParams,
    ) -> types.CallToolResult:
        arguments = params.arguments or {}
        if params.name == GUIDE_TOOL_NAME:
            task = arguments.get("task")
            if not isinstance(task, str) or not task.strip():
                return _call_result(
                    {"error": {"code": "invalid_tool_input", "message": "task is required"}},
                    is_error=True,
                )
            payload = application.guide.recommend(
                task=task,
                effective_tool_ids=application.effective_tool_ids,
                business_category=_optional_string(arguments.get("business_category")),
                as_of=_optional_string(arguments.get("as_of")),
            )
            return _call_result(payload)
        if params.name == READ_TOOL_NAME:
            alias = arguments.get("alias")
            if not isinstance(alias, str):
                return _call_result(
                    {"error": {"code": "invalid_tool_input", "message": "alias is required"}},
                    is_error=True,
                )
            observation = application.observations.read_observation(alias)
            if observation is None:
                return _call_result(
                    {
                        "error": {
                            "code": "observation_not_found",
                            "message": "alias is invalid or outside this attempt",
                        }
                    },
                    is_error=True,
                )
            return _call_result(
                {
                    "alias": observation.alias,
                    "block_id": observation.block_id,
                    "title": observation.title,
                    "content": observation.content,
                    "block_type": observation.block_type,
                    "source_locator": observation.source_locator,
                    "source_coordinates": observation.source_coordinates,
                    "content_hash": observation.content_hash,
                    "provenance": {
                        "provider": observation.provider,
                        "tool_name": observation.tool_name,
                        "method_version": observation.method_version,
                    },
                }
            )
        contract = application.contracts.get_by_mcp_name(params.name)
        if contract is None or contract.canonical_tool_id not in application.effective_tool_ids:
            return _call_result(
                {"error": {"code": "tool_not_allowed", "message": "tool is not exposed"}},
                is_error=True,
            )
        result = await asyncio.to_thread(
            application.execution.execute,
            contract.canonical_tool_id,
            arguments,
        )
        return _call_result(result.model_dump(mode="json"))

    return Server(
        "doxagent-data-mcp",
        version="1.0.0",
        instructions=application.instructions(),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


def main() -> None:
    pilot_env_file = os.environ.get("DOXAGENT_PILOT_ENV_FILE")
    if pilot_env_file:
        load_dotenv(pilot_env_file, override=False)
    token = _required_env("DOXAGENT_DATA_MCP_CAPABILITY")
    public_key = _required_env("DOXAGENT_DATA_MCP_PUBLIC_KEY")
    control_root = Path(_required_env("DOXAGENT_OBSERVATION_CONTROL_ROOT"))
    claims = DataCapabilityCodec.verify(token, public_key=public_key)
    application = DataMcpApplication(
        claims=claims,
        run_root=Path.cwd().resolve(),
        control_root=control_root,
    )
    server = build_server(application)

    async def run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                server.create_initialization_options(),
            )

    asyncio.run(run())


def _call_result(payload: dict[str, Any], *, is_error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(
        content=[
            types.TextContent(
                type="text",
                text=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
            )
        ],
        structured_content=payload,
        is_error=is_error,
    )


def _read_annotations(*, idempotent: bool) -> types.ToolAnnotations:
    return types.ToolAnnotations(
        read_only_hint=True,
        destructive_hint=False,
        idempotent_hint=idempotent,
        open_world_hint=True,
    )


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


if __name__ == "__main__":
    main()
