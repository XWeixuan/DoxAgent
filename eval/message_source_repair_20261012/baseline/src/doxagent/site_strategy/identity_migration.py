"""Idempotent Browser Identity migration and first-wave desired configuration."""

from __future__ import annotations

from dataclasses import dataclass

from .repository import SiteStrategyRepository
from .schema import (
    AccessCombination,
    AuthState,
    BrowserIdentityAccess,
    BrowserIdentityLifecycle,
    BrowserIdentitySpec,
    BrowserResidency,
    BrowserRuntimeKind,
    SiteStrategySpec,
    utc_now,
)


@dataclass(frozen=True)
class IdentityMigrationChange:
    kind: str
    resource_id: str
    detail: str


def legacy_identity_id(profile_id: str) -> str:
    return profile_id


def plan_legacy_identity_migration(
    repository: SiteStrategyRepository,
) -> list[IdentityMigrationChange]:
    changes: list[IdentityMigrationChange] = []
    for head, spec in repository.list_active_strategies():
        if not head.enabled:
            continue
        for combination in spec.access.combinations:
            if combination.identity_id:
                continue
            assert combination.profile_id and combination.egress_id
            identity_id = legacy_identity_id(combination.profile_id)
            if repository.get_identity(identity_id) is None:
                changes.append(
                    IdentityMigrationChange(
                        "identity",
                        identity_id,
                        f"managed {combination.profile_id}/{combination.egress_id}",
                    )
                )
            auth = repository.get_site_identity_auth(spec.site_id, identity_id)
            profile = repository.get_profile(combination.profile_id)
            if (
                profile
                and auth.auth_state is AuthState.UNKNOWN
                and profile.auth_state is not AuthState.UNKNOWN
            ):
                changes.append(
                    IdentityMigrationChange(
                        "auth", f"{spec.site_id}/{identity_id}", profile.auth_state.value
                    )
                )
    return changes


def migrate_legacy_identities(
    repository: SiteStrategyRepository, *, actor: str = "migration:browser-identity-v1"
) -> list[IdentityMigrationChange]:
    """Create one-to-one Managed identities without rewriting Site revisions."""
    changes = plan_legacy_identity_migration(repository)
    for head, spec in repository.list_active_strategies():
        if not head.enabled:
            continue
        for combination in spec.access.combinations:
            identity_id = combination.identity_id or legacy_identity_id(
                combination.profile_id or combination.combination_id
            )
            if repository.get_identity(identity_id) is None:
                if not combination.profile_id or not combination.egress_id:
                    raise ValueError(
                        f"combination {combination.combination_id} references missing identity"
                    )
                profile = repository.get_profile(combination.profile_id)
                if profile is None:
                    raise ValueError(f"missing legacy profile: {combination.profile_id}")
                repository.apply_identity(
                    BrowserIdentitySpec(
                        identity_id=identity_id,
                        runtime_kind=BrowserRuntimeKind.MANAGED_PLAYWRIGHT,
                        profile_id=profile.profile_id,
                        egress_id=combination.egress_id,
                        environment=profile.environment,
                        browser_release="playwright-cft-153",
                        lifecycle=BrowserIdentityLifecycle(
                            residency=BrowserResidency.ON_DEMAND,
                            idle_seconds=43_200,
                        ),
                        access=BrowserIdentityAccess(
                            max_concurrency=1,
                            min_interval_ms=max(500, spec.access.min_interval_ms),
                        ),
                        credential_ref=profile.credential_ref,
                    ),
                    expected_revision=None,
                    actor=actor,
                )
            profile = repository.get_profile(combination.profile_id or "")
            existing_auth = repository.get_site_identity_auth(spec.site_id, identity_id)
            if profile and existing_auth.auth_state is AuthState.UNKNOWN:
                repository.save_site_identity_auth(
                    existing_auth.model_copy(
                        update={
                            "auth_state": profile.auth_state,
                            "verification_url": spec.auth.verification_url,
                            "observed_session_revision": profile.session_revision,
                            "verified_at": utc_now()
                            if profile.auth_state is AuthState.VALID
                            else None,
                            "reason_code": "migrated_legacy_profile",
                        }
                    )
                )
    return changes


def with_identity_bindings(spec: SiteStrategySpec, mapping: dict[str, str]) -> SiteStrategySpec:
    """Return a new Site revision whose combinations reference identities."""
    combinations = []
    for value in spec.access.combinations:
        identity_id = mapping.get(value.combination_id, value.identity_id)
        if identity_id is None:
            identity_id = legacy_identity_id(value.profile_id or value.combination_id)
        combinations.append(
            AccessCombination(
                id=value.combination_id,
                identity_id=identity_id,
                priority=value.priority,
                enabled=value.enabled,
            )
        )
    return spec.model_copy(
        update={"access": spec.access.model_copy(update={"combinations": combinations})}
    )


__all__ = [
    "IdentityMigrationChange",
    "legacy_identity_id",
    "migrate_legacy_identities",
    "plan_legacy_identity_migration",
    "with_identity_bindings",
]
