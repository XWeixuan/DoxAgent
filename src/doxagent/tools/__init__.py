"""Permission-aware mock tool boundary."""

from doxagent.tools.client import ToolClient
from doxagent.tools.factory import default_real_tool_registry
from doxagent.tools.mock import MockToolClient, default_tool_registry
from doxagent.tools.registry import ToolDescriptor, ToolRegistry
from doxagent.tools.schema import ToolError, ToolRequest, ToolResult

__all__ = [
    "MockToolClient",
    "ToolClient",
    "ToolDescriptor",
    "ToolError",
    "ToolRegistry",
    "ToolRequest",
    "ToolResult",
    "default_real_tool_registry",
    "default_tool_registry",
]
