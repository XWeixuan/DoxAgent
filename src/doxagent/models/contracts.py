"""Shared tool permission contract."""

from pydantic import BaseModel, ConfigDict, Field

from doxagent.models.ids import NonEmptyStr


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AgentPermissions(ContractModel):
    readable_context_scopes: list[NonEmptyStr] = Field(default_factory=list)
    writable_targets: list[NonEmptyStr] = Field(default_factory=list)
    allowed_tools: list[NonEmptyStr] = Field(default_factory=list)
    can_raise_objection: bool = False
    can_delegate: bool = False
    can_propose_patch: bool = False
    can_access_private_memory: bool = False
