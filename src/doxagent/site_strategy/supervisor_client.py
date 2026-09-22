"""Restricted JSON-lines client for the local Chrome supervisor Unix socket."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .schema import BrowserIdentitySpec, ProxyEgress


@dataclass(frozen=True)
class SupervisorInstance:
    identity_id: str
    instance_id: str
    generation: int
    pid: int
    cdp_http_url: str
    cdp_websocket_url: str
    display: str


class ChromeSupervisorClient:
    def __init__(self, socket_path: str | Path, *, owner_id: str) -> None:
        self.socket_path = str(socket_path)
        self.owner_id = owner_id

    async def _request(self, command: str, payload: dict[str, object]) -> dict[str, object]:
        unix_connector_name = "open_unix_connection"
        open_unix_connection: Any = getattr(asyncio, unix_connector_name)
        deadline = time.monotonic() + 15
        while True:
            try:
                reader, writer = await open_unix_connection(self.socket_path)
                break
            except (FileNotFoundError, ConnectionRefusedError) as exc:
                if time.monotonic() >= deadline:
                    raise RuntimeError("chrome_supervisor_unavailable") from exc
                await asyncio.sleep(0.2)
        try:
            writer.write(
                (
                    json.dumps({"command": command, "owner_id": self.owner_id, **payload}) + "\n"
                ).encode()
            )
            await writer.drain()
            async with asyncio.timeout(30):
                raw = await reader.readline()
            if not raw:
                raise RuntimeError("chrome_supervisor_closed_connection")
            response = json.loads(raw)
            if not response.get("ok"):
                raise RuntimeError(str(response.get("error") or "chrome_supervisor_error"))
            return dict(response)
        finally:
            writer.close()
            await writer.wait_closed()

    async def ensure(
        self, identity: BrowserIdentitySpec, egress: ProxyEgress
    ) -> SupervisorInstance:
        response = await self._request(
            "ensure",
            {
                "identity": identity.model_dump(mode="json"),
                "egress": egress.model_dump(mode="json"),
            },
        )
        return SupervisorInstance(
            identity_id=identity.identity_id,
            instance_id=str(response["instance_id"]),
            generation=int(str(response["generation"])),
            pid=int(str(response["pid"])),
            cdp_http_url=str(response["cdp_http_url"]),
            cdp_websocket_url=str(response["cdp_websocket_url"]),
            display=str(response["display"]),
        )

    async def status(self, identity_id: str) -> dict[str, object]:
        return await self._request("status", {"identity_id": identity_id})

    async def stop(self, identity_id: str, *, reason: str) -> bool:
        response = await self._request("stop", {"identity_id": identity_id, "reason": reason})
        return bool(response.get("stopped"))

    async def release_controller(self, identity_id: str) -> None:
        await self._request("release_controller", {"identity_id": identity_id})

    async def select_maintenance(self, identity_id: str) -> int:
        response = await self._request("maintenance_select", {"identity_id": identity_id})
        return int(str(response["vnc_port"]))


__all__ = ["ChromeSupervisorClient", "SupervisorInstance"]
