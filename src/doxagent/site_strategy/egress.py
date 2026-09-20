"""Mihomo fixed-listener rendering and egress verification."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from ipaddress import ip_address
from typing import Any

import httpx
import yaml

from .repository import SiteStrategyRepository
from .schema import ProxyEgress


def render_fixed_listeners(config_text: str, egresses: list[ProxyEgress]) -> str:
    """Merge deterministic HTTP listeners without replacing the subscription config."""
    config = yaml.safe_load(config_text) or {}
    if not isinstance(config, dict):
        raise ValueError("Mihomo config root must be a mapping")
    listeners = config.get("listeners") or []
    if not isinstance(listeners, list):
        raise ValueError("Mihomo listeners must be a list")
    managed_names = {f"doxagent-{item.egress_id}" for item in egresses}
    retained = [
        item
        for item in listeners
        if not isinstance(item, dict) or str(item.get("name", "")) not in managed_names
    ]
    managed = [
        {
            "name": f"doxagent-{item.egress_id}",
            "type": "http",
            "port": item.listener_port,
            "listen": "0.0.0.0",
            "proxy": item.node_ref,
        }
        for item in sorted(egresses, key=lambda value: value.listener_port)
        if item.enabled
    ]
    config["listeners"] = [*retained, *managed]
    return yaml.safe_dump(config, allow_unicode=True, sort_keys=False)


async def probe_egress(
    repository: SiteStrategyRepository,
    egress_id: str,
    *,
    ip_urls: tuple[str, ...] = (
        "https://api.ipify.org?format=json",
        "https://ifconfig.co/json",
    ),
    timeout_seconds: float = 15,
) -> ProxyEgress:
    egress = repository.get_egress(egress_id)
    if egress is None:
        raise KeyError(egress_id)
    status = "UNAVAILABLE"
    observed_ip: str | None = None
    probe_endpoint: str | None = None
    async with httpx.AsyncClient(proxy=egress.endpoint, timeout=timeout_seconds) as client:
        for ip_url in ip_urls:
            try:
                response = await client.get(ip_url)
                response.raise_for_status()
                payload: Any = response.json()
                observed_ip = str(payload.get("ip") or "").strip() or None
                if observed_ip and ip_address(observed_ip).version == 4:
                    status = "READY"
                    probe_endpoint = ip_url
                    break
                observed_ip = None
            except (httpx.HTTPError, ValueError):
                continue
    changed = observed_ip is not None and observed_ip != egress.observed_ip
    updated = egress.model_copy(
        update={
            "status": status,
            "observed_ip": observed_ip,
            "observed_at": datetime.now(UTC),
            "probe_endpoint": probe_endpoint,
            "generation": egress.generation + int(changed),
            "node_fingerprint": hashlib.sha256(egress.node_ref.encode()).hexdigest(),
        }
    )
    repository.upsert_egress(updated)
    return updated


__all__ = ["probe_egress", "render_fixed_listeners"]
