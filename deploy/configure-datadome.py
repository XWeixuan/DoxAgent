"""Root-only idempotent bootstrap. API key on stdin, no secrets in args/output/Git.

Prepare image first. With --enable, solver is enabled only after operator firewall
approval; real egress preflight still gates every paid submission.
"""

import argparse
import json
import os
import re
import secrets
import sqlite3
import subprocess
import sys
from pathlib import Path


def squid_config(bindings, listeners, subnet):
    lines = [
        "http_port 18791",
        "visible_hostname doxagent-captcha-proxy",
        "pid_filename /tmp/squid.pid",
        "coredump_dir /tmp",
        "cache_mem 8 MB",
        "cache deny all",
        "access_log stdio:/dev/stdout captcha",
        "cache_log /dev/stderr",
        "pinger_enable off",
        "logfile_rotate 0",
        "auth_param basic program /usr/lib/squid/basic_ncsa_auth /config/passwords",
        "auth_param basic children 2",
        "auth_param basic realm DataDome-Egress",
        "auth_param basic credentialsttl 30 minutes",
        "acl CONNECT method CONNECT",
        "acl SSL_ports port 443",
        "acl solver_src src 138.201.188.166/32 " + subnet,
        "acl allowed dstdomain .reuters.com .captcha-delivery.com .datadome.co api.ipify.org",
        "acl private_dst dst 0.0.0.0/8 10.0.0.0/8 127.0.0.0/8 169.254.0.0/16 172.16.0.0/12 192.168.0.0/16 ::1/128 fc00::/7 fe80::/10",
        "acl too_many maxconn 16",
        "http_access deny !solver_src",
        "http_access deny !CONNECT",
        "http_access deny !SSL_ports",
        "http_access deny !allowed",
        "http_access deny private_dst",
        "http_access deny too_many",
    ]
    # log only identity, result and bytes; no full URL, request headers or credentials.
    lines.insert(6, "logformat captcha %ts.%03tu %>a %Ss/%03>Hs %<st %rm %un")
    for n, (identity_id, binding) in enumerate(sorted(bindings.items())):
        user = binding["proxyLogin"]
        host, port = listeners[binding["egress_id"]]
        if not re.fullmatch(r"[a-z0-9-]+", user) or not re.fullmatch(r"[a-z0-9.-]+", host):
            raise ValueError("unsafe proxy binding")
        lines.extend(
            [
                f"acl identity{n} proxy_auth {user}",
                f"http_access allow identity{n}",
                f"cache_peer {host} parent {int(port)} 0 no-query no-digest proxy-only name=egress{n}",
                f"cache_peer_access egress{n} allow identity{n}",
                f"cache_peer_access egress{n} deny all",
            ]
        )
    lines.extend(
        [
            "http_access deny all",
            "never_direct allow all",
            "forwarded_for delete",
            "via off",
            "connect_timeout 10 seconds",
            "request_timeout 15 seconds",
            "read_timeout 120 seconds",
            "shutdown_lifetime 3 seconds",
            "delay_pools 1",
            "delay_class 1 1",
            "delay_parameters 1 1048576/5242880",
            "delay_access 1 allow all",
        ]
    )
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--public-host", required=True)
    parser.add_argument("--enable", action="store_true")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise SystemExit("root required")
    import ipaddress

    if not ipaddress.ip_address(args.public_host).is_global:
        raise SystemExit("public IPv4 address required")
    key = sys.stdin.readline().strip()
    if not re.fullmatch(r"[a-fA-F0-9]{32}", key):
        raise SystemExit("API key must be provided on stdin")
    data = Path("/var/lib/docker/volumes/doxagent-v2_v2-site-strategy/_data")
    root = Path("/var/lib/doxagent/captcha-proxy")
    root.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chown(root, 0, 13)
    root.chmod(0o750)
    config_path = data / "credentials/datadome-solver.json"
    old = json.loads(config_path.read_text()) if config_path.exists() else {}
    config = {
        "enabled": args.enable,
        "api_key": key,
        "daily_tasks": 20,
        "sites": ["reuters"],
        "bindings": {},
    }
    listeners = {}
    from urllib.parse import urlsplit

    with sqlite3.connect(f"file:{data}/registry/site_strategy.sqlite3?mode=ro", uri=True) as db:
        scope = json.loads(
            db.execute(
                "select data_json from source_access_scopes where scope_id=?",
                ("source:reuters_site_search",),
            ).fetchone()[0]
        )
        for identity_id in [scope["primary_identity_id"], scope["fallback_identity_id"]]:
            head = db.execute(
                "select active_revision from browser_identity_heads where identity_id=?",
                (identity_id,),
            ).fetchone()
            identity = json.loads(
                db.execute(
                    "select spec_json from browser_identity_revisions where identity_id=? "
                    "and revision=?",
                    (identity_id, head[0]),
                ).fetchone()[0]
            )
            egress = json.loads(
                db.execute(
                    "select data_json from proxy_egresses where egress_id=?",
                    (identity["egress_id"],),
                ).fetchone()[0]
            )
            endpoint = urlsplit(egress["endpoint"])
            if endpoint.hostname != "doxagent-egress-clash" or endpoint.scheme != "http":
                raise SystemExit("only the registered local Clash endpoint is supported")
            listeners[identity["egress_id"]] = (endpoint.hostname, endpoint.port)
            prior = old.get("bindings", {}).get(identity_id, {})
            config["bindings"][identity_id] = {
                "egress_id": identity["egress_id"],
                "proxyAddress": args.public_host,
                "localAddress": "doxagent-captcha-proxy",
                "proxyPort": 18791,
                "proxyLogin": prior.get("proxyLogin", "dd-" + secrets.token_hex(8)),
                "proxyPassword": prior.get("proxyPassword", secrets.token_urlsafe(32)),
            }
    network = json.loads(
        subprocess.check_output(["docker", "network", "inspect", "doxagent-v2_default"])
    )[0]
    subnet = network["IPAM"]["Config"][0]["Subnet"]
    ipaddress.ip_network(subnet)
    passwords = []
    for b in config["bindings"].values():
        hashed = (
            subprocess.check_output(
                ["openssl", "passwd", "-6", "-stdin"], input=(b["proxyPassword"] + "\n").encode()
            )
            .decode()
            .strip()
        )
        passwords.append(b["proxyLogin"] + ":" + hashed)
    for name, content in [
        ("passwords", "\n".join(passwords) + "\n"),
        ("squid.conf", squid_config(config["bindings"], listeners, subnet)),
    ]:
        path = root / name
        path.write_text(content)
        os.chown(path, 0, 13)
        path.chmod(0o640)
    config_path.write_text(json.dumps(config))
    os.chown(config_path, 10001, 10001)
    config_path.chmod(0o600)
    print(
        json.dumps(
            {
                "configured": True,
                "enabled": args.enable,
                "identities": list(config["bindings"]),
                "port": 18791,
                "secrets_printed": False,
            }
        )
    )


if __name__ == "__main__":
    main()
