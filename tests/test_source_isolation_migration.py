"""Migration must preserve identity data and never turn subscriptions on."""

import sqlite3

import pytest

from doxagent.site_strategy.governance_repository import GovernanceRepository
from doxagent.site_strategy.isolation_migration import apply_migration, migration_plan
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.seeds import bootstrap_seed
from doxagent.site_strategy.service import SiteStrategyService


def test_preview_is_read_only_and_apply_is_idempotent(tmp_path):
    path = tmp_path / "registry.sqlite3"
    repository = SiteStrategyRepository(path)
    service = SiteStrategyService(repository, profile_root=tmp_path / "profiles")
    bootstrap_seed(repository, service)
    before_profiles = {p.profile_id: p.model_dump() for p in repository.list_profiles()}
    readonly = SiteStrategyRepository(path, read_only=True)
    try:
        preview = migration_plan(readonly)
        with pytest.raises(sqlite3.OperationalError):
            readonly._connection.execute("delete from browser_profiles")
    finally:
        readonly.close()
    result = apply_migration(
        repository,
        preview,
        expected_revision=preview["expected_revision"],
        backup_path=tmp_path / "migration-backup.sqlite3",
    )
    assert result["fallbacks_certified"] == 0
    scopes = GovernanceRepository(repository).list_scopes()
    owners = [
        identity
        for scope in scopes
        for identity in (
            scope.primary_identity_id,
            scope.fallback_identity_id,
        )
        if identity
    ]
    assert len(owners) == len(set(owners))
    by_source = {scope.source_id: scope for scope in scopes if scope.source_id}
    assert (
        by_source["trendforce_news"].primary_identity_id
        != by_source["trendforce_press_releases"].primary_identity_id
    )
    assert (
        by_source["globenewswire_search"].primary_identity_id
        != by_source["globenewswire_semiconductors_rss"].primary_identity_id
    )
    assert not by_source["ctee_semiconductor"].enabled
    assert all(item["action"] == "KEEP" for item in migration_plan(repository)["scopes"])
    after_profiles = {p.profile_id: p.model_dump() for p in repository.list_profiles()}
    assert all(after_profiles[key] == value for key, value in before_profiles.items())
    with pytest.raises(RuntimeError, match="Registry changed"):
        apply_migration(
            repository,
            preview,
            expected_revision=preview["expected_revision"],
            backup_path=tmp_path / "not-written.sqlite3",
        )
    repository.close()
