"""Render DoxAgent fixed listeners into a subscribed Mihomo config."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml  # type: ignore[import-untyped]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("config")
    parser.add_argument("manifest")
    parser.add_argument("output")
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    output_path = Path(args.output).resolve()
    manifest_path = Path(args.manifest).resolve()
    config = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(config, dict) or not isinstance(manifest, list):
        raise SystemExit("config must be YAML mapping and manifest must be a JSON list")
    listeners = config.get("listeners") or []
    if not isinstance(listeners, list):
        raise SystemExit("Mihomo listeners must be a list")
    names = {f"doxagent-{value['egress_id']}" for value in manifest}
    config["listeners"] = [
        value
        for value in listeners
        if not isinstance(value, dict) or value.get("name") not in names
    ] + [
        {
            "name": f"doxagent-{value['egress_id']}",
            "type": "http",
            "port": int(value["listener_port"]),
            "listen": "0.0.0.0",
            "rule": f"doxagent-egress-{value['egress_id']}",
        }
        for value in manifest
        if value.get("enabled", True)
    ]
    sub_rules = config.get("sub-rules") or {}
    if not isinstance(sub_rules, dict):
        raise SystemExit("Mihomo sub-rules must be a mapping")
    for name in list(sub_rules):
        if str(name).startswith("doxagent-egress-"):
            sub_rules.pop(name)
    private_rules = [
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
    ]
    for value in manifest:
        if value.get("enabled", True):
            sub_rules[f"doxagent-egress-{value['egress_id']}"] = [
                *private_rules,
                f"MATCH,{value['node_ref']}",
            ]
    config["sub-rules"] = sub_rules
    rendered = yaml.safe_dump(config, allow_unicode=True, sort_keys=False)
    yaml.safe_load(rendered)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_suffix(output_path.suffix + ".tmp")
    temporary.write_text(rendered, encoding="utf-8")
    temporary.replace(output_path)
    print(json.dumps({"output": str(output_path), "listeners": len(config["listeners"])}))


if __name__ == "__main__":
    main()
