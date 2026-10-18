"""Validate the actual Unix-socket supervisor's network namespace, not DNS luck."""

import asyncio
import os

from .supervisor_client import ChromeSupervisorClient


async def network_binding(socket_path):
    if not socket_path:
        return {"ready": True, "mode": "standalone"}
    try:
        async with asyncio.timeout(1):
            status = await ChromeSupervisorClient(socket_path, owner_id="readiness")._request(
                "health", {}
            )
        expected = status.get("network_namespace")
        actual = os.readlink("/proc/self/ns/net")
        return {
            "ready": bool(expected and expected == actual),
            "reason": "ok" if expected == actual else "supervisor_network_namespace_mismatch",
            "site_namespace": actual,
            "supervisor_namespace": expected,
        }
    except (OSError, RuntimeError, TimeoutError):
        return {"ready": False, "reason": "supervisor_network_binding_unavailable"}
