"""Run inside Site Access. Preview by default; --apply uses the Owner APIs/CAS.

No publisher/provider requests, no credential values in output, no Profile/egress
replacement. Lifecycle-only changes preserve the existing Supervisor instance.
"""

import argparse
import copy
import json
import os
import sqlite3
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from doxagent.settings import DoxAgentSettings
from doxagent.site_strategy.credentials import CredentialStore


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    settings = DoxAgentSettings()
    token = Path(settings.site_access_admin_token_file).read_text().strip()

    def api(path, payload=None):
        request = urllib.request.Request(
            "http://127.0.0.1:8011" + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            method="POST" if payload is not None else "GET",
        )
        with urllib.request.urlopen(request, timeout=45) as response:
            return json.load(response)

    path = Path(settings.site_access_sqlite_path).resolve()
    db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    db.execute("pragma query_only=on")
    scope = next(s for s in api("/v2/scopes") if s["source_id"] == "reuters_site_search")
    site = api("/v1/sites/reuters")
    changes = []
    available = []
    for identity_id in (scope["primary_identity_id"], scope.get("fallback_identity_id")):
        if not identity_id:
            continue
        spec = json.loads(
            db.execute(
                "select r.spec_json from browser_identity_revisions r "
                "join browser_identity_heads h on h.identity_id=r.identity_id and h.active_revision=r.revision "
                "where h.identity_id=?",
                (identity_id,),
            ).fetchone()[0]
        )
        ref = spec.get("credential_ref")
        if not ref:
            profile = json.loads(
                db.execute(
                    "select data_json from browser_profiles where profile_id=?",
                    (spec["profile_id"],),
                ).fetchone()[0]
            )
            ref = profile.get("credential_ref")
        if ref:
            # status() never exports the password. Do not persist even its username label.
            try:
                available.append(
                    bool(
                        CredentialStore(settings.site_access_credential_root).status(ref)[
                            "available"
                        ]
                    )
                )
            except (ValueError, OSError):
                available.append(False)
        else:
            available.append(False)
        lifecycle = copy.deepcopy(spec)
        lifecycle["lifecycle"]["idle_seconds"] = max(900, lifecycle["lifecycle"]["idle_seconds"])
        if lifecycle != spec:
            changes.append(
                (
                    "/v1/identities:apply",
                    {
                        "spec": lifecycle,
                        "expected_revision": spec["revision"],
                        "actor": "reuters-reliability-20261026",
                    },
                )
            )
    newsite = copy.deepcopy(site)
    url = newsite["auth"].get("verification_url")
    if url and urlsplit(url).path.startswith("/site-search"):
        newsite["auth"]["verification_url"] = None
    # Only enable bounded login if at least one existing private binding is available.
    # A fallback without a credential remains public-only and never gets a secret copied.
    newsite["auth"]["auto_login"] = any(available)
    if newsite != site:
        changes.append(
            (
                "/v1/sites:apply",
                {
                    "spec": newsite,
                    "expected_revision": site["revision"],
                    "actor": "reuters-reliability-20261026",
                },
            )
        )
    newscope = copy.deepcopy(scope)
    body = newscope.get("verification_plan", {}).get("body_url")
    if body and urlsplit(body).path.startswith("/site-search"):
        newscope["verification_plan"].pop("body_url", None)
    bodyplan = newscope.get("verification_plan", {}).get("body", {})
    if (
        isinstance(bodyplan, dict)
        and bodyplan.get("url")
        and urlsplit(bodyplan["url"]).path.startswith("/site-search")
    ):
        bodyplan.pop("url")
    if newscope != scope:
        changes.append(
            ("/v2/scopes:apply", {"spec": newscope, "expected_revision": scope["revision"]})
        )
    summary = {
        "apply": args.apply,
        "credential_available_by_member": available,
        "changes": [
            {
                "path": p,
                "resource": v["spec"].get("identity_id") or v["spec"].get("scope_id") or "reuters",
                "expected_revision": v["expected_revision"],
            }
            for p, v in changes
        ],
    }
    if args.apply and changes:
        root = path.parent / "backups"
        root.mkdir(exist_ok=True, mode=0o700)
        backup = root / (
            "reuters-reliability-"
            + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            + ".sqlite3"
        )
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        with sqlite3.connect(backup) as target:
            db.backup(target)
        summary["backup"] = str(backup)
        summary["applied"] = []
        for endpoint, payload in changes:
            result = api(endpoint, payload)
            summary["applied"].append({"path": endpoint, "revision": result.get("revision")})
    db.close()
    print(json.dumps(summary))


if __name__ == "__main__":
    main()
