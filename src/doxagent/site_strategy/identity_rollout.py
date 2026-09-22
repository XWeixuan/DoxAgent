"""Explicit first-wave dual-runtime rollout; dry-run unless --apply is supplied."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from .identity_migration import migrate_legacy_identities, with_identity_bindings
from .repository import SiteStrategyRepository
from .schema import (
    BrowserEnvironment,
    BrowserIdentityAccess,
    BrowserIdentityLifecycle,
    BrowserIdentitySpec,
    BrowserProfile,
    BrowserResidency,
    BrowserRuntimeKind,
)


@dataclass(frozen=True)
class DesiredIdentity:
    identity_id: str
    profile_id: str
    egress_id: str
    residency: BrowserResidency
    timezone_id: str
    owner_site_id: str


FIRST_WAVE = (
    DesiredIdentity(
        "dowjones-main",
        "dowjones-main",
        "us-standard-5",
        BrowserResidency.ALWAYS_ON,
        "America/Los_Angeles",
        "barrons",
    ),
    DesiredIdentity(
        "dowjones-backup",
        "dowjones-backup",
        "jp-standard-6",
        BrowserResidency.ON_DEMAND,
        "Asia/Tokyo",
        "barrons",
    ),
    DesiredIdentity(
        "seeking-alpha-main",
        "seeking-alpha-main",
        "us-standard-5",
        BrowserResidency.ALWAYS_ON,
        "America/Los_Angeles",
        "seeking_alpha",
    ),
    DesiredIdentity(
        "seeking-alpha-backup",
        "seeking-alpha-backup",
        "jp-standard-6",
        BrowserResidency.ON_DEMAND,
        "Asia/Tokyo",
        "seeking_alpha",
    ),
    DesiredIdentity(
        "yahoo-main",
        "yahoo-main",
        "jp-standard-6",
        BrowserResidency.ALWAYS_ON,
        "Asia/Tokyo",
        "yahoo_finance",
    ),
    DesiredIdentity(
        "yahoo-backup",
        "yahoo-backup",
        "us-standard-5",
        BrowserResidency.ON_DEMAND,
        "America/Los_Angeles",
        "yahoo_finance",
    ),
)

SITE_MAPPINGS = {
    "barrons": {"barrons-1": "dowjones-main", "barrons-2": "dowjones-backup"},
    "wsj": {"wsj-1": "dowjones-main", "wsj-2": "dowjones-backup"},
    "marketwatch": {
        "marketwatch-1": "dowjones-main",
        "marketwatch-2": "dowjones-backup",
    },
    "seeking_alpha": {
        "seeking_alpha-1": "seeking-alpha-main",
        "seeking_alpha-2": "seeking-alpha-backup",
    },
    "yahoo_finance": {
        "yahoo_finance-1": "yahoo-main",
        "yahoo_finance-2": "yahoo-backup",
    },
}


def rollout_plan(repository: SiteStrategyRepository) -> dict[str, object]:
    identities: list[dict[str, object]] = []
    for desired in FIRST_WAVE:
        current = repository.get_identity(desired.identity_id)
        identities.append(
            {
                "identity_id": desired.identity_id,
                "action": "create" if current is None else "keep",
                "current_revision": current.revision if current else None,
                "profile_id": desired.profile_id,
                "egress_id": desired.egress_id,
            }
        )
    sites: list[dict[str, object]] = []
    for site_id, mapping in SITE_MAPPINGS.items():
        spec = repository.get_strategy(site_id)
        sites.append(
            {
                "site_id": site_id,
                "current_revision": spec.revision if spec else None,
                "action": "update"
                if spec
                and any(
                    item.combination_id in mapping
                    and item.identity_id != mapping[item.combination_id]
                    for item in spec.access.combinations
                )
                else "keep",
                "mapping": mapping,
            }
        )
    return {"identities": identities, "sites": sites}


def apply_first_wave(repository: SiteStrategyRepository) -> dict[str, object]:
    migrate_legacy_identities(repository)
    result = rollout_plan(repository)
    for desired in FIRST_WAVE:
        egress = repository.get_egress(desired.egress_id)
        if egress is None:
            raise ValueError(f"missing egress: {desired.egress_id}")
        profile = repository.get_profile(desired.profile_id)
        if profile is None:
            repository.save_profile(
                BrowserProfile(
                    profile_id=desired.profile_id,
                    site_id=desired.owner_site_id,
                    bound_egress_id=desired.egress_id,
                    directory_key=desired.profile_id,
                    environment=BrowserEnvironment(timezone_id=desired.timezone_id),
                )
            )
        elif profile.bound_egress_id != desired.egress_id:
            raise ValueError(f"profile egress conflict: {desired.profile_id}")
        current = repository.get_identity(desired.identity_id)
        if current is None:
            repository.apply_identity(
                BrowserIdentitySpec(
                    identity_id=desired.identity_id,
                    runtime_kind=BrowserRuntimeKind.EXTERNAL_CHROME,
                    profile_id=desired.profile_id,
                    egress_id=desired.egress_id,
                    environment=BrowserEnvironment(timezone_id=desired.timezone_id),
                    browser_release="google-chrome-153.0.8010.52",
                    lifecycle=BrowserIdentityLifecycle(
                        residency=desired.residency,
                        idle_seconds=43_200,
                    ),
                    access=BrowserIdentityAccess(max_concurrency=1, min_interval_ms=3_000),
                ),
                expected_revision=None,
                actor="rollout:dual-runtime-v1",
            )
        elif (
            current.runtime_kind is not BrowserRuntimeKind.EXTERNAL_CHROME
            or current.profile_id != desired.profile_id
            or current.egress_id != desired.egress_id
        ):
            raise ValueError(f"existing identity conflicts with rollout: {desired.identity_id}")
    for site_id, mapping in SITE_MAPPINGS.items():
        spec = repository.get_strategy(site_id)
        if spec is None:
            raise ValueError(f"site strategy missing: {site_id}")
        updated = with_identity_bindings(spec, mapping)
        if updated.access.combinations != spec.access.combinations:
            repository.apply_strategy(
                updated,
                expected_revision=spec.revision,
                actor="rollout:dual-runtime-v1",
            )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--backup")
    args = parser.parse_args()
    repository = SiteStrategyRepository(Path(args.database))
    try:
        plan = rollout_plan(repository)
        if args.apply:
            if not args.backup:
                raise SystemExit("--backup is required with --apply")
            repository.backup_to(args.backup)
            plan = apply_first_wave(repository)
        print(json.dumps(plan, ensure_ascii=False, indent=2, default=str))
    finally:
        repository.close()


if __name__ == "__main__":
    main()


__all__ = ["FIRST_WAVE", "SITE_MAPPINGS", "apply_first_wave", "rollout_plan"]
