"""Tool compatibility helpers for the MAF runtime boundary."""

from typing import Any, Literal

from doxagent.models import AgentTask, ResultStatus, ToolCallSummary
from doxagent.tools import ToolRegistry, ToolRequest, ToolResult, default_tool_registry

ToolMode = Literal["disabled", "mock", "real"]


class ToolRegistryFunctionAdapter:
    """Wrap DoxAgent ToolRegistry behind a small callable boundary."""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    def call_tool(
        self,
        *,
        tool_name: str,
        task: AgentTask,
        input_payload: dict[str, Any] | None = None,
    ) -> ToolResult:
        return self.registry.call(
            ToolRequest(
                tool_name=tool_name,
                ticker=task.ticker,
                agent_name=task.agent_name,
                input=input_payload or {},
                metadata={
                    "run_id": task.run_metadata.run_id,
                    "task_id": task.task_id,
                    "task_type": task.task_type.value,
                    "workflow_node": task.run_metadata.workflow_node,
                },
            ),
            task.permissions,
        )


def resolve_tool_registry(
    tool_mode: ToolMode,
    tool_registry: ToolRegistry | None,
) -> ToolRegistry | None:
    if tool_mode == "disabled":
        return None
    if tool_registry is not None:
        return tool_registry
    if tool_mode == "mock":
        return default_tool_registry()
    return ToolRegistry()


def requested_tool_calls(task: AgentTask) -> list[dict[str, Any]]:
    raw = task.input_context.get("tool_requests", [])
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("input_context['tool_requests'] must be a list.")
    result: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict) or "tool_name" not in item:
            raise ValueError("Each tool request must be a dict with tool_name.")
        result.append(item)
    return result


def tool_result_to_summary(result: ToolResult) -> ToolCallSummary:
    return ToolCallSummary(
        tool_name=result.tool_name,
        status=result.status,
        input_summary="runtime tool request",
        output_summary=result.output_summary,
        evidence_refs=result.evidence_refs,
    )


def has_required_tool_failure(task: AgentTask, results: list[ToolResult]) -> bool:
    required = task.input_context.get("required_tool_names", [])
    if not isinstance(required, list):
        return False
    required_names = {item for item in required if isinstance(item, str)}
    return any(
        result.tool_name in required_names and result.status is ResultStatus.FAILED
        for result in results
    )
