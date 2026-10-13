#!/usr/bin/python3
"""Refresh root-only subscription candidates; never rotate live identity bindings."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, urlopen

import yaml

ROOT = Path("/opt/doxagent-egress-clash/providers")
MAX_BYTES = 4_000_000


def atomic_write(path: Path, body: bytes) -> None:
    fd, temporary = tempfile.mkstemp(prefix=".refresh-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), 0o600)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def normalize(provider: str, content: bytes) -> tuple[bytes, list[str]]:
    document = yaml.safe_load(content)
    proxies = document.get("proxies") if isinstance(document, dict) else None
    if not isinstance(proxies, list) or not proxies:
        raise ValueError("invalid_subscription_proxies")
    rows, fingerprints, names = [], [], set()
    for proxy in proxies:
        if not isinstance(proxy, dict) or not proxy.get("name") or not proxy.get("server"):
            raise ValueError("invalid_subscription_node")
        node = dict(proxy)
        node["name"] = provider + ":" + str(node["name"])
        if node["name"] in names:
            raise ValueError("duplicate_subscription_node")
        names.add(node["name"])
        rows.append(node)
        endpoint = f"{node.get('type')}|{node['server']}|{node.get('port')}"
        fingerprints.append(hashlib.sha256(endpoint.encode()).hexdigest())
    return yaml.safe_dump({"proxies": rows}, allow_unicode=True).encode(), fingerprints


def refresh(root: Path = ROOT) -> dict:
    sources = json.loads((root / "sources.json").read_text())
    previous_path = root / "status.json"
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    report = {}
    for provider in ("SSRDOG", "Flower_SS"):
        row = dict(previous.get(provider, {}))
        row["attempted_at"] = datetime.now(UTC).isoformat()
        try:
            url = sources[provider]
            if not isinstance(url, str) or not url.startswith("https://"):
                raise ValueError("invalid_subscription_url")
            with urlopen(
                Request(url, headers={"User-Agent": "clash.meta"}), timeout=20
            ) as response:
                content = response.read(MAX_BYTES + 1)
                usage = response.headers.get("subscription-userinfo", "")
            if len(content) > MAX_BYTES:
                raise ValueError("subscription_too_large")
            normalized, fingerprints = normalize(provider, content)
            atomic_write(root / (provider + ".yaml"), normalized)
            row.update(
                status="READY",
                stale=False,
                refreshed_at=row["attempted_at"],
                sha256=hashlib.sha256(normalized).hexdigest(),
                node_count=len(fingerprints),
                endpoint_fingerprints=fingerprints,
            )
            row.pop("error", None)
            row.pop("http_status", None)
            row["usage"] = {
                key.strip(): int(value.strip())
                for field in usage.split(";")
                if "=" in field
                for key, value in [field.split("=", 1)]
                if key.strip() in ("upload", "download", "total", "expire")
                and value.strip().isdigit()
            }
        except Exception as exc:
            # Retain the last parseable snapshot; never log the credential URL.
            row.update(status="STALE", stale=True, error=type(exc).__name__)
            if isinstance(getattr(exc, "code", None), int):
                row["http_status"] = exc.code
        report[provider] = row
    atomic_write(previous_path, json.dumps(report, indent=2).encode())
    return {
        name: {
            key: value
            for key, value in row.items()
            if key not in ("endpoint_fingerprints", "usage")
        }
        for name, row in report.items()
    }


if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit("root_required")
    print(json.dumps(refresh()))
