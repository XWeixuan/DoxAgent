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
    """Merge deterministic, fail-closed HTTP listeners into a subscription config.

    Each managed listener uses its own sub-rule.  Keeping ``proxy`` on a listener
    would bypass that rule, so the fixed node is instead the terminal MATCH action.
    """
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
    sub_rules = config.get("sub-rules") or {}
    if not isinstance(sub_rules, dict):
        raise ValueError("Mihomo sub-rules must be a mapping")
    for name in list(sub_rules):
        if name.startswith("doxagent-egress-"):
            sub_rules.pop(name)
    managed = [
        {
            "name": f"doxagent-{item.egress_id}",
            "type": "http",
            "port": item.listener_port,
            "listen": "0.0.0.0",
            "rule": f"doxagent-egress-{item.egress_id}",
        }
        for item in sorted(egresses, key=lambda value: value.listener_port)
        if item.enabled
    ]
    for item in sorted(egresses, key=lambda value: value.listener_port):
        if item.enabled:
            sub_rules[f"doxagent-egress-{item.egress_id}"] = [
                "IP-CIDR,0.0.0.0/8,REJECT",
                "IP-CIDR,10.0.0.0/8,REJECT",
                "IP-CIDR,100.64.0.0/10,REJECT",
                "IP-CIDR,127.0.0.0/8,REJECT",
                "IP-CIDR,169.254.0.0/16,REJECT",
                "IP-CIDR,172.16.0.0/12,REJECT",
                "IP-CIDR,192.0.0.0/24,REJECT",
                "IP-CIDR,192.0.2.0/24,REJECT",
                "IP-CIDR,192.168.0.0/16,REJECT",
                "IP-CIDR,198.51.100.0/24,REJECT",
                "IP-CIDR,203.0.113.0/24,REJECT",
                "IP-CIDR,224.0.0.0/4,REJECT",
                "IP-CIDR,240.0.0.0/4,REJECT",
                "IP-CIDR6,::/128,REJECT",
                "IP-CIDR6,::1/128,REJECT",
                "IP-CIDR6,::ffff:0:0/96,REJECT",
                "IP-CIDR6,2001:db8::/32,REJECT",
                "IP-CIDR6,fc00::/7,REJECT",
                "IP-CIDR6,fe80::/10,REJECT",
                "IP-CIDR6,ff00::/8,REJECT",
                f"MATCH,{item.node_ref}",
            ]
    config["listeners"] = [*retained, *managed]
    config["sub-rules"] = sub_rules
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
