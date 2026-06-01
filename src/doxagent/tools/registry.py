"""Permission-aware tool registry."""

from pydantic import BaseModel, ConfigDict, Field

from doxagent.models import AgentPermissions, ResultStatus
from doxagent.tools.client import ToolClient
from doxagent.tools.schema import ToolError, ToolRequest, ToolResult


class ToolDescriptor(BaseModel):
    """Model-facing metadata for a registered tool."""

    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    input_fields: list[str] = Field(default_factory=list)
    business_purpose: str | None = None
    concurrent_safe: bool = True
    compactable: bool = True


class ToolRegistry:
    def __init__(self) -> None:
        self._clients: dict[str, ToolClient] = {}
        self._descriptors: dict[str, ToolDescriptor] = {}

    def register(
        self,
        tool_name: str,
        client: ToolClient,
        *,
        descriptor: ToolDescriptor | None = None,
    ) -> None:
        self._clients[tool_name] = client
        self._descriptors[tool_name] = descriptor or ToolDescriptor(
            name=tool_name,
            description=f"{tool_name} tool.",
        )

    def names(self) -> list[str]:
        return sorted(self._clients)

    def describe(self, tool_name: str) -> ToolDescriptor | None:
        descriptor = self._descriptors.get(tool_name)
        return descriptor.model_copy(deep=True) if descriptor is not None else None

    def describe_allowed(self, permissions: AgentPermissions) -> list[ToolDescriptor]:
        descriptors: list[ToolDescriptor] = []
        for tool_name in permissions.allowed_tools:
            descriptor = self.describe(tool_name)
            if descriptor is not None:
                descriptors.append(descriptor)
        return descriptors

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
