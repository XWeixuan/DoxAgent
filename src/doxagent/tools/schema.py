"""Tool request and result contracts."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from doxagent.models import AgentName, NonEmptyStr, ResultStatus


class ToolModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolRequest(ToolModel):
    tool_name: NonEmptyStr
    ticker: NonEmptyStr
    agent_name: AgentName
    input: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ToolError(ToolModel):
    code: NonEmptyStr
    message: NonEmptyStr
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class ToolResult(ToolModel):
    tool_name: NonEmptyStr
    status: ResultStatus
    output: dict[str, Any] = Field(default_factory=dict)
    output_summary: NonEmptyStr | None = None
    raw: Any | None = None
    error: ToolError | None = None

    @property
    def succeeded(self) -> bool:
        return self.status is ResultStatus.SUCCEEDED and self.error is None
