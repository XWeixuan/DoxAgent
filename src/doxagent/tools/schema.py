"""Tool request and result contracts."""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from doxagent.models import (
    AgentName,
    EvidenceRef,
    EvidenceSourceType,
    NonEmptyStr,
    ResultStatus,
    new_id,
)


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
    evidence_refs: list[EvidenceRef] = Field(default_factory=list)
    error: ToolError | None = None

    @property
    def succeeded(self) -> bool:
        return self.status is ResultStatus.SUCCEEDED and self.error is None

    def to_evidence_ref(
        self,
        *,
        source_type: EvidenceSourceType = EvidenceSourceType.TOOL_RESULT,
        source_id: str | None = None,
        title: str | None = None,
        citation_scope: str = "tool_result",
        confidence: float = 0.5,
    ) -> EvidenceRef:
        return EvidenceRef(
            evidence_id=new_id("evidence"),
            source_type=source_type,
            source_id=source_id or self.tool_name,
            title=title or f"{self.tool_name} 工具结果",
            summary=self.output_summary or "工具已返回结果。",
            retrieval_metadata={"tool_name": self.tool_name, "status": self.status.value},
            confidence=confidence,
            citation_scope=citation_scope,
        )
