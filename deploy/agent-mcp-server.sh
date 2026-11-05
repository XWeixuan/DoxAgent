#!/bin/sh
# The MCP child needs only its internal API credential.
unset CONTROL_PLANE_API_KEY
exec python -m doxagent.agent_mcp
