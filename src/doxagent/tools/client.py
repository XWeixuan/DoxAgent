"""Tool client protocol."""

from typing import Protocol

from doxagent.tools.schema import ToolRequest, ToolResult


class ToolClient(Protocol):
    def call(self, request: ToolRequest) -> ToolResult:
        """Call a tool and return a normalized result."""
