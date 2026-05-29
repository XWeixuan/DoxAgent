"""Permission-aware tool registry."""

from doxagent.models import AgentPermissions, ResultStatus
from doxagent.tools.client import ToolClient
from doxagent.tools.schema import ToolError, ToolRequest, ToolResult


class ToolRegistry:
    def __init__(self) -> None:
        self._clients: dict[str, ToolClient] = {}

    def register(self, tool_name: str, client: ToolClient) -> None:
        self._clients[tool_name] = client

    def names(self) -> list[str]:
        return sorted(self._clients)

    def call(self, request: ToolRequest, permissions: AgentPermissions) -> ToolResult:
        if request.tool_name not in permissions.allowed_tools:
            return ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.FAILED,
                error=ToolError(
                    code="tool_not_allowed",
                    message=f"Agent is not allowed to call tool: {request.tool_name}",
                    retryable=False,
                ),
            )
        client = self._clients.get(request.tool_name)
        if client is None:
            return ToolResult(
                tool_name=request.tool_name,
                status=ResultStatus.FAILED,
                error=ToolError(
                    code="tool_not_registered",
                    message=f"Tool is not registered: {request.tool_name}",
                    retryable=False,
                ),
            )
        return client.call(request)
