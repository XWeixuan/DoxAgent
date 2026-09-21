#!/usr/bin/env python3
"""Controlled Site Access rollout helper.

The helper reads the root-owned admin token, never prints it, and only calls the
fixed Site Access administration endpoints needed for browser/egress acceptance.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8091"
DEFAULT_TOKEN_FILE = Path("/etc/doxagent/site-access-admin-token")


class SiteAccessAdmin:
    def __init__(self, base_url: str, token_file: Path) -> None:
        token = token_file.read_text(encoding="utf-8").strip()
        if not token:
            raise RuntimeError("Site Access admin token file is empty")
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=45,
        )

    def close(self) -> None:
        self.client.close()

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self.client.request(method, path, **kwargs)
        if response.is_error:
            detail = response.text[:500]
            raise RuntimeError(f"{method} {path} failed ({response.status_code}): {detail}")
        return response.json()


def _sites(admin: SiteAccessAdmin) -> list[dict[str, Any]]:
    return [item for item in admin.request("GET", "/v1/sites") if item["head"]["enabled"]]


def _direct_profile(site_id: str, primary: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    return {
        "profile_id": f"{site_id}-direct-1"[:128],
        "site_id": site_id,
        "bound_egress_id": "server-direct",
        "directory_key": f"{site_id}-direct-1"[:128],
        "credential_ref": primary.get("credential_ref"),
        "login_url": primary.get("login_url"),
        "auth_state": "UNKNOWN",
        "operational_state": "AVAILABLE",
        "operational_revision": 0,
        "maintenance_session_id": None,
        "environment": {
            "locale": "en-US",
            "timezone_id": "Asia/Singapore",
            "window_width": 1440,
            "window_height": 1000,
            "revision": 1,
        },
        "session_revision": 0,
        "created_at": now,
        "updated_at": now,
    }


def prepare_direct(admin: SiteAccessAdmin) -> None:
    egresses = {item["egress_id"]: item for item in admin.request("GET", "/v1/egresses")}
    if "server-direct" not in egresses:
        raise RuntimeError("server-direct egress is missing; seed migration did not complete")
    direct = admin.request("POST", "/v1/egresses/server-direct:probe")
    if direct["status"] != "READY":
        raise RuntimeError("server-direct egress probe did not become READY")
    profiles = admin.request("GET", "/v1/profiles")
    by_id = {item["profile_id"]: item for item in profiles}
    by_site: dict[str, list[dict[str, Any]]] = {}
    for profile in profiles:
        by_site.setdefault(profile["site_id"], []).append(profile)
    for item in _sites(admin):
        spec = item["spec"]
        site_id = spec["site_id"]
        if site_id == "generic":
            continue
        site_profiles = sorted(by_site.get(site_id, []), key=lambda value: value["profile_id"])
        if not site_profiles:
            raise RuntimeError(f"{site_id} has no primary Profile to clone")
        profile = _direct_profile(site_id, site_profiles[0])
        if profile["profile_id"] not in by_id:
            admin.request(
                "PUT", f"/v1/profiles/{profile['profile_id']}", json=profile
            )
        combination_id = f"{site_id}-direct-1"[:128]
        combinations = spec["access"]["combinations"]
        if any(value["combination_id"] == combination_id for value in combinations):
            continue
        combinations.append(
            {
                "combination_id": combination_id,
                "profile_id": profile["profile_id"],
                "egress_id": "server-direct",
                "priority": 900,
                "enabled": False,
            }
        )
        admin.request(
            "POST",
            "/v1/sites:apply",
            json={
                "spec": spec,
                "expected_revision": spec["revision"],
                "actor": "rollout:direct-candidate",
                "enabled": True,
            },
        )
        print(json.dumps({"site_id": site_id, "direct_candidate": "prepared"}))


def probe_all(admin: SiteAccessAdmin, *, direct_only: bool) -> int:
    profiles = admin.request("GET", "/v1/profiles")
    by_site: dict[str, list[dict[str, Any]]] = {}
    for profile in profiles:
        if direct_only and profile["bound_egress_id"] != "server-direct":
            continue
        by_site.setdefault(profile["site_id"], []).append(profile)
    failures = 0
    for item in _sites(admin):
        spec = item["spec"]
        site_id = spec["site_id"]
        if site_id == "generic":
            continue
        probe_url = spec["access"].get("probe_url") or spec["auth"].get(
            "maintenance_url"
        )
        if not probe_url:
            print(json.dumps({"site_id": site_id, "result": "NO_PROBE_URL"}))
            failures += 1
            continue
        for profile in sorted(by_site.get(site_id, []), key=lambda value: value["profile_id"]):
            try:
                result = admin.request(
                    "POST",
                    f"/v1/profiles/{profile['profile_id']}:probe",
                    json={"url": probe_url},
                )
                record = {
                    "site_id": site_id,
                    "profile_id": profile["profile_id"],
                    "egress_id": profile["bound_egress_id"],
                    "status_code": result.get("status_code"),
                    "disposition": result.get("disposition"),
                    "failure_category": result.get("failure_category"),
                    "reason_code": result.get("reason_code"),
                    "final_url": result.get("final_url"),
                    "exit_ip": result.get("exit_ip_observation"),
                }
                if result.get("status_code") != 200:
                    failures += 1
                print(json.dumps(record, ensure_ascii=False))
            except Exception as exc:
                failures += 1
                print(
                    json.dumps(
                        {
                            "site_id": site_id,
                            "profile_id": profile["profile_id"],
                            "result": "ERROR",
                            "error": str(exc),
                        },
                        ensure_ascii=False,
                    )
                )
    return failures


def activate_direct(admin: SiteAccessAdmin, site_ids: set[str]) -> None:
    known = {item["spec"]["site_id"] for item in _sites(admin)}
    unknown = sorted(site_ids - known)
    if unknown:
        raise RuntimeError(f"unknown site IDs: {unknown}")
    for item in _sites(admin):
        spec = item["spec"]
        site_id = spec["site_id"]
        if site_id not in site_ids:
            continue
        changed = False
        for combination in spec["access"]["combinations"]:
            if combination["egress_id"] == "server-direct" and not combination["enabled"]:
                combination["enabled"] = True
                changed = True
        if not changed:
            continue
        admin.request(
            "POST",
            "/v1/sites:apply",
            json={
                "spec": spec,
                "expected_revision": spec["revision"],
                "actor": "rollout:activate-direct",
                "enabled": True,
            },
        )
        print(json.dumps({"site_id": site_id, "direct_candidate": "enabled"}))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare-direct")
    probe = commands.add_parser("probe-all")
    probe.add_argument("--direct-only", action="store_true")
    activate = commands.add_parser("activate-direct")
    activate.add_argument("site_ids", nargs="+")
    args = parser.parse_args()
    admin = SiteAccessAdmin(args.base_url, args.token_file)
    try:
        if args.command == "prepare-direct":
            prepare_direct(admin)
            return 0
        if args.command == "probe-all":
            return 0 if probe_all(admin, direct_only=args.direct_only) == 0 else 2
        activate_direct(admin, set(args.site_ids))
        return 0
    finally:
        admin.close()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(f"site-access-rollout: {error}", file=sys.stderr)
        raise SystemExit(1) from None
