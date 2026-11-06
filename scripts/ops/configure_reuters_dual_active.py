"""Run inside Site Access; preview by default, Owner/CAS apply, no browser requests."""
import argparse
import copy
import json
import urllib.request
from pathlib import Path

from doxagent.settings import DoxAgentSettings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--disable", action="store_true")
    args = parser.parse_args()
    settings = DoxAgentSettings()
    token = Path(settings.site_access_admin_token_file).read_text().strip()

    def api(path, payload=None):
        request = urllib.request.Request("http://127.0.0.1:8011" + path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
            method="POST" if payload is not None else "GET")
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)

    scope = next(s for s in api("/v2/scopes") if s["scope_id"] == "source:reuters_site_search")
    if not scope.get("fallback_identity_id"):
        raise ValueError("dual_active_requires_two_registered_members")
    updated = copy.deepcopy(scope)
    if args.disable:
        updated["verification_plan"].pop("crawler_routing", None)
    else:
        updated["verification_plan"]["crawler_routing"] = "dual_active"
    changed = scope != updated
    if args.apply and changed:
        updated = api("/v2/scopes:apply", {"spec": updated, "expected_revision": scope["revision"]})
    print(json.dumps({"changed": changed, "applied": bool(args.apply and changed),
        "revision": updated["revision"], "mode": updated["verification_plan"].get("crawler_routing", "failover"),
        "members": [updated["primary_identity_id"], updated["fallback_identity_id"]]}))


if __name__ == "__main__":
    main()
