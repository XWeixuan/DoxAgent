"""O2 V2 capability ceiling: local workspace plus conditional built-in web search only."""

from __future__ import annotations

from doxagent.codex_runtime.schema import CodexEventLibraryAgentRole, CodexEventLibraryNode


def allowed_data_tools(
    node: CodexEventLibraryNode, role: CodexEventLibraryAgentRole
) -> frozenset[str]:
    del node, role
    return frozenset()


MAINTENANCE_CONTROL_TOOLS = frozenset(
    {"publish_working_revision", "discard_working_revision", "export_version"}
)
