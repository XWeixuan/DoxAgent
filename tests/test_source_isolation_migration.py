"""Migration must preserve identity data and never turn subscriptions on."""

import sqlite3

import pytest

from doxagent.site_strategy.governance_repository import GovernanceRepository
from doxagent.site_strategy.isolation_migration import apply_migration, migration_plan
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.seeds import bootstrap_seed
from doxagent.site_strategy.service import SiteStrategyService


def test_existing_backup_cannot_hide_same_ip_under_verified_redundancy(tmp_path):
    from doxagent.site_strategy.schema import BrowserIdentitySpec, ProxyEgress, utc_now

    repository = SiteStrategyRepository(tmp_path / "registry.sqlite3")
    service = SiteStrategyService(repository, profile_root=tmp_path / "profiles")
    bootstrap_seed(repository, service)
    plan = migration_plan(repository)
    item = next(s for s in plan["scopes"] if s["scope"]["source_id"] == "yahoo_finance_news")
    primary_id = item["scope"]["primary_identity_id"]
    primary = repository.get_identity(primary_id)
    if primary is None:
        primary = BrowserIdentitySpec.model_validate(
            next(v["spec"] for v in item["identities"] if v["spec"]["identity_id"] == primary_id)
        )
        primary = repository.apply_identity(primary, expected_revision=None, actor="test")
    main_egress = repository.get_egress(primary.egress_id)
    repository.upsert_egress(
        main_egress.model_copy(
            update={"observed_ip": "8.8.4.4", "status": "READY", "node_ref": "fixed"}
        )
    )
    repository.upsert_egress(
        ProxyEgress(
            egress_id="geo-backup",
            endpoint="http://127.0.0.1:12346",
            node_ref="fixed",
            node_fingerprint="fixed",
            listener_port=12346,
            observed_ip="1.1.1.1",
            status="READY",
        )
    )
    with repository.transaction() as connection:
        for name, address in [(primary.egress_id, "8.8.4.4"), ("geo-backup", "1.1.1.1")]:
            connection.execute(
                "insert into egress_regions values(?,?,?,?)",
                (name, address, "DE", utc_now().isoformat()),
            )
    repository.apply_identity(
        BrowserIdentitySpec.model_validate(
            {**primary.model_dump(), "identity_id": "yahoo-backup", "profile_id": "old-backup"}
        ),
        expected_revision=None,
        actor="test",
    )
    migrated = next(
        s
        for s in migration_plan(repository)["scopes"]
        if s["scope"]["source_id"] == "yahoo_finance_news"
    )
    assert migrated["scope"]["fallback_identity_id"] == "yahoo-backup-isolated"
    backup = next(
        v["spec"]
        for v in migrated["identities"]
        if v["spec"]["identity_id"] == "yahoo-backup-isolated"
    )
    assert backup["egress_id"] == "geo-backup"
    assert repository.get_identity("yahoo-backup").profile_id == "old-backup"
    repository.close()


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
