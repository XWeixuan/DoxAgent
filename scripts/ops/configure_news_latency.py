"""Preview/apply the five-source latency configuration through existing owners.

Run in the Message Bus environment. Dry-run opens both databases read-only.
Site/Identity writes use the running Site owner API and revision CAS. Controller
detach drains reads but retains Supervisor Chrome, profiles, and maintenance pages.
"""

import argparse
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from doxagent.v2_read.native_content import NativeContent

TARGETS = {
    "yahoo_finance_news": "yahoo_finance",
    "investorshub_ticker_news": "investorshub",
    "investing_ticker_news": "investing",
    "barrons_ticker_news": "barrons",
    "globenewswire_search": "globenewswire",
}


def read(path, query):
    with sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        return [dict(row) for row in db.execute(query)]


def plan(bus_path, registry_path, *, site_capacity=8, validated_identity_capacity=2):
    if not 1 <= validated_identity_capacity <= site_capacity <= 8:
        raise ValueError("capacity requires 1 <= validated identity <= site <= 8")
    codec = NativeContent(bus_path)
    sources = [
        codec.decode(json.loads(r["data_json"]))
        for r in read(bus_path, "SELECT data_json FROM source_definitions")
    ]
    groups = {s["scheduler_group"] for s in sources if s["source_id"] in TARGETS}
    changes = []
    for source in sources:
        if source["scheduler_group"] not in groups:
            continue
        patch = {
            "scheduler_constraints": {
                **source["scheduler_constraints"],
                "max_concurrency": site_capacity,
                "capacity_mode": "adaptive",
                # Transport/host and Site/Identity own actual request pacing.
                # A second Bus-wide gap would serialize independent ticker heads.
                "minimum_request_gap_seconds": 0,
            }
        }
        if source["source_id"] == "globenewswire_search":
            patch["default_parameters"] = {
                **source["default_parameters"],
                "http_listing_verified": True,
            }
        if source["source_id"] == "yahoo_finance_news":
            patch["default_polling_config"] = {
                **source["default_polling_config"],
                "target_interval_seconds": 30,
            }
        if source["source_id"] in TARGETS:
            properties = dict(source.get("parameter_schema", {}).get("properties", {}))
            if source["source_id"] in {
                "globenewswire_search",
                "investorshub_ticker_news",
                "investing_ticker_news",
            }:
                properties["http_listing_verified"] = {"type": "boolean"}
            patch["parameter_schema"] = {**source["parameter_schema"], "properties": properties}
        if any(source.get(k) != v for k, v in patch.items()):
            changes.append(
                {
                    "kind": "source",
                    "id": source["source_id"],
                    "expected_revision": source["version"],
                    "old": source,
                    "patch": patch,
                }
            )
    codec = NativeContent(bus_path)
    for row in read(
        bus_path,
        "SELECT data_json FROM ticker_source_bindings WHERE "
        "source_id='yahoo_finance_news' AND tombstoned_at IS NULL",
    ):
        binding = codec.decode(json.loads(row["data_json"]))
        if binding["polling"]["target_interval_seconds"] == 60:
            changes.append(
                {
                    "kind": "binding",
                    "id": binding["binding_id"],
                    "expected_revision": binding["version"],
                    "old": binding,
                    "patch": {"polling": {**binding["polling"], "target_interval_seconds": 30}},
                }
            )
    sites = read(
        registry_path,
        "SELECT r.spec_json FROM site_strategy_heads h JOIN "
        "site_strategy_revisions r ON r.site_id=h.site_id AND "
        "r.revision=h.active_revision",
    )
    identities = {
        json.loads(r["spec_json"])["identity_id"]: json.loads(r["spec_json"])
        for r in read(
            registry_path,
            "SELECT r.spec_json FROM browser_identity_heads h JOIN "
            "browser_identity_revisions r ON r.identity_id=h.identity_id AND "
            "r.revision=h.active_revision",
        )
    }
    runtimes = {
        r["identity_id"]: json.loads(r["data_json"])
        for r in read(registry_path, "SELECT identity_id,data_json FROM identity_runtime")
    }
    desired_identities = set()
    scoped_sites = set()
    for row in read(registry_path, "SELECT data_json FROM source_access_scopes"):
        scope = json.loads(row["data_json"])
        if scope.get("source_id") in TARGETS:
            scoped_sites.add(scope["owner_site_id"])
            desired_identities.update(
                i
                for i in (scope.get("primary_identity_id"), scope.get("fallback_identity_id"))
                if i
            )
    for row in sites:
        old = json.loads(row["spec_json"])
        if old["site_id"] not in TARGETS.values():
            continue
        desired = {
            **old,
            "access": {
                **old["access"],
                "max_concurrency": site_capacity,
                "min_interval_ms": min(old["access"]["min_interval_ms"], 500),
                "body_queue_timeout_ms": 3000,
                "crawler_queue_timeout_ms": 3000,
            },
        }
        if old["site_id"] == "yahoo_finance":
            desired["body"] = {**old["body"], "access_order": ["http_public", "browser", "reader"]}
        if old["site_id"] == "investorshub":
            desired["body"] = {**old["body"], "access_order": ["browser", "http_public", "reader"]}
        if desired != old:
            changes.append(
                {
                    "kind": "site",
                    "id": old["site_id"],
                    "expected_revision": old["revision"],
                    "old": old,
                    "spec": desired,
                }
            )
        if old["site_id"] not in scoped_sites:
            desired_identities.update(
                c.get("identity_id") or c.get("profile_id") for c in old["access"]["combinations"]
            )
    for identity_id in sorted(desired_identities):
        old = identities.get(identity_id)
        desired_access = (
            {
                **old["access"],
                "max_concurrency": max(
                    old["access"]["max_concurrency"], validated_identity_capacity
                ),
                "min_interval_ms": min(old["access"]["min_interval_ms"], 500),
            }
            if old
            else None
        )
        if old and old["access"] != desired_access:
            runtime = runtimes.get(identity_id, {})
            skip = (
                "identity_not_available"
                if runtime.get("operational_state")
                in {"MAINTENANCE", "DRAINING", "UNHEALTHY", "PROFILE_BUSY"}
                else None
            )
            if runtime.get("probation_until") and datetime.fromisoformat(
                runtime["probation_until"]
            ) > datetime.now(UTC):
                skip = "identity_probation"
            changes.append(
                {
                    "kind": "identity",
                    "id": identity_id,
                    "expected_revision": old["revision"],
                    "old": old,
                    "spec": {**old, "access": desired_access},
                    **({"skip_reason": skip} if skip else {}),
                }
            )
    # Source default updates can cascade binding revisions. Apply explicit binding
    # overrides first so our own source update cannot invalidate their CAS.
    changes.sort(
        key=lambda change: {"binding": 0, "source": 1, "site": 2, "identity": 3}[change["kind"]]
    )
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bus", required=True)
    parser.add_argument("--registry", required=True)
    parser.add_argument("--site-url")
    parser.add_argument("--site-admin-token-file")
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--site-capacity", type=int, default=8)
    parser.add_argument(
        "--validated-identity-capacity",
        type=int,
        default=2,
        help="Use 3/4 only after a same-identity canary; this is a validated ceiling.",
    )
    args = parser.parse_args()
    changes = plan(
        args.bus,
        args.registry,
        site_capacity=args.site_capacity,
        validated_identity_capacity=args.validated_identity_capacity,
    )
    receipt = {
        "created_at": datetime.now(UTC).isoformat(),
        "apply": args.apply,
        "changes": changes,
        "applied": [],
    }
    destination = Path(args.receipt)
    destination.parent.mkdir(parents=True, exist_ok=True)

    def save():
        destination.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding="utf-8")

    save()
    if args.apply:
        import httpx

        if not args.site_url or not args.site_admin_token_file:
            parser.error(
                "--apply requires --site-url and --site-admin-token-file for the live Site owner"
            )
        from doxagent.message_bus_v2.repository import MessageBusV2Repository
        from doxagent.message_bus_v2.schema import UpdateActor
        from doxagent.message_bus_v2.service import MessageBusV2Service

        repository = MessageBusV2Repository(args.bus)
        service = MessageBusV2Service(repository)
        token = Path(args.site_admin_token_file).read_text().strip()
        try:
            with httpx.Client(
                base_url=args.site_url, headers={"Authorization": "Bearer " + token}, timeout=60
            ) as client:
                for change in changes:
                    if change.get("skip_reason"):
                        continue
                    if change["kind"] in {"source", "binding"}:
                        with repository.atomic_intake():
                            old = (
                                service.require_source(change["id"])
                                if change["kind"] == "source"
                                else repository.get_binding(change["id"])
                            )
                            if old.version != change["expected_revision"]:
                                raise RuntimeError("source_revision_conflict:" + change["id"])
                            apply = (
                                service.update_source
                                if change["kind"] == "source"
                                else service.update_binding
                            )
                            current = apply(
                                change["id"],
                                change["patch"],
                                actor=UpdateActor.SYSTEM,
                                reason="news-latency-v1",
                            )
                            new_revision = current.version
                    else:
                        endpoint = (
                            "/v1/sites:apply"
                            if change["kind"] == "site"
                            else "/v1/identities:apply"
                        )
                        response = client.post(
                            endpoint,
                            json={
                                "spec": change["spec"],
                                "expected_revision": change["expected_revision"],
                                "actor": "news-latency-v1",
                            },
                        )
                        response.raise_for_status()
                        new_revision = response.json()["revision"]
                    receipt["applied"].append(
                        {"kind": change["kind"], "id": change["id"], "revision": new_revision}
                    )
                    save()
        except Exception as exc:
            receipt["error"] = type(exc).__name__ + ":" + str(exc).split("\n", 1)[0]
            save()
            raise
    print(
        json.dumps(
            {
                "apply": args.apply,
                "changes": [
                    {
                        "kind": c["kind"],
                        "id": c["id"],
                        "revision": c["expected_revision"],
                        **({"skip_reason": c["skip_reason"]} if c.get("skip_reason") else {}),
                    }
                    for c in changes
                ],
                "receipt": str(destination),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
