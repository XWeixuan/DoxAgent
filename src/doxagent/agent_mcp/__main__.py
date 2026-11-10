"""Read-only data tools plus an optional owner-only WeChat notification."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from pydantic import ValidationError

from .client import MAX_RESULT_BYTES, ApiClient, ReadError, encoded
from .notifications import DESCRIPTION, TOOL_NAME, NotificationInput, WeChatNotifications
from .schemas import TOOLS
from .service import AgentReads


def build_server(reads, notifications=None):
    tool_specs = dict(TOOLS)
    if notifications is not None:
        tool_specs[TOOL_NAME] = (NotificationInput, DESCRIPTION)

    async def list_tools(_context, _params):
        return types.ListToolsResult(
            tools=[
                types.Tool(
                    name=name,
                    description=description,
                    input_schema=model.model_json_schema(),
                    output_schema={
                        "type": "object",
                        "properties": {
                            "data": {"type": "object"},
                            "meta": {"type": "object"},
                            "error": {"type": "object"},
                        },
                        "anyOf": [{"required": ["data", "meta"]}, {"required": ["error"]}],
                        "additionalProperties": False,
                    },
                    annotations=types.ToolAnnotations(
                        read_only_hint=name != TOOL_NAME,
                        destructive_hint=False,
                        idempotent_hint=name != TOOL_NAME,
                        open_world_hint=name == TOOL_NAME,
                    ),
                )
                for name, (model, description) in tool_specs.items()
            ]
        )

    async def call_tool(_context, params):
        error = False
        try:
            if params.name == TOOL_NAME and notifications is not None:
                payload = await notifications.send(params.arguments or {})
            else:
                payload = await reads.call(params.name, params.arguments or {})
            if len(encoded(payload)) > MAX_RESULT_BYTES:
                raise ReadError("RESOURCE_TOO_LARGE", "Select a smaller page or Case section.")
        except ValidationError:
            payload = ReadError(
                "VALIDATION_FAILED", "Invalid tool fields or parameter combination."
            ).wire()
            error = True
        except ReadError as exc:
            payload, error = exc.wire(), True
        except Exception as exc:
            logging.getLogger(__name__).error("Agent tool failed (%s)", type(exc).__name__)
            payload, error = (
                ReadError(
                    "READ_FAILED", "Read failed; inspect the service log.", retryable=True
                ).wire(),
                True,
            )
        # Avoid serializing a second copy of large content into TextContent.
        return types.CallToolResult(content=[], structured_content=payload, is_error=error)

    return Server(
        "doxagent-agent-readonly",
        version="1.1.0",
        instructions=(
            "Read operational KPIs first, list/search one ticker's Cases next, "
            "and read only selected Case details. DoxAgent data access is read-only; "
            "no commands, configuration, trading or independent research access. "
            "When available, send_wechat_notification is the separate outbound "
            "tool for reports to the fixed owner. It changes no DoxAgent data. "
            "Notification acceptance does not confirm WeChat delivery."
        ),
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


async def run():
    token = Path(os.environ["DOXAGENT_AGENT_READ_TOKEN_FILE"]).read_text(encoding="utf-8").strip()
    if len(token) < 32:
        raise ValueError("A configured internal read credential is required")
    api = ApiClient(os.getenv("DOXAGENT_AGENT_API_URL", "http://v2-api:8000"), token)
    notifications = None
    try:
        notification_token_file = os.getenv("DOXAGENT_PUSHPLUS_TOKEN_FILE")
        if notification_token_file:
            notifications = WeChatNotifications(
                Path(notification_token_file).read_text(encoding="utf-8")
            )
        server = build_server(AgentReads(api), notifications)
        async with stdio_server() as (reader, writer):
            await server.run(reader, writer, server.create_initialization_options())
    finally:
        await api.close()
        if notifications is not None:
            await notifications.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(run())
