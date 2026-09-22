from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from doxagent.site_strategy.browser_runtime import BrowserRuntimeManager
from doxagent.site_strategy.budget import JointBudget
from doxagent.site_strategy.identity_migration import with_identity_bindings
from doxagent.site_strategy.identity_rollout import apply_first_wave, rollout_plan
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.schema import (
    AuthState,
    BrowserIdentitySpec,
    BrowserRuntimeKind,
    SiteIdentityAuth,
    SitePurpose,
)
from doxagent.site_strategy.seeds import bootstrap_seed
from doxagent.site_strategy.service import SiteStrategyService


@pytest.fixture
def seeded(tmp_path: Path) -> SiteStrategyService:
    repository = SiteStrategyRepository(tmp_path / "registry.sqlite3")
    service = SiteStrategyService(repository, profile_root=tmp_path / "profiles")
    bootstrap_seed(repository, service)
    yield service
    repository.close()


def test_bootstrap_migrates_legacy_combinations_without_rewriting_sites(
    seeded: SiteStrategyService,
) -> None:
    barrons = seeded.repository.get_strategy("barrons")
    identity = seeded.repository.get_identity("barrons-1")
    assert barrons is not None and barrons.revision == 1
    assert barrons.access.combinations[0].identity_id is None
    assert identity is not None
    assert identity.runtime_kind is BrowserRuntimeKind.MANAGED_PLAYWRIGHT
    assert identity.profile_id == "barrons-1"


def test_first_wave_shares_dowjones_identity_but_not_auth_state(
    seeded: SiteStrategyService,
) -> None:
    apply_first_wave(seeded.repository)
    second_plan = rollout_plan(seeded.repository)
    assert all(item["action"] == "keep" for item in second_plan["identities"])
    assert all(item["action"] == "keep" for item in second_plan["sites"])
    for site_id in ("barrons", "wsj", "marketwatch"):
        spec = seeded.repository.get_strategy(site_id)
        assert spec is not None
        assert spec.access.combinations[0].identity_id == "dowjones-main"
        assert spec.access.combinations[0].profile_id is None

    seeded.repository.save_site_identity_auth(
        SiteIdentityAuth(
            site_id="barrons",
            identity_id="dowjones-main",
            auth_state=AuthState.VALID,
        )
    )
    assert (
        seeded.repository.get_site_identity_auth("barrons", "dowjones-main").auth_state
        is AuthState.VALID
    )
    assert (
        seeded.repository.get_site_identity_auth("wsj", "dowjones-main").auth_state
        is AuthState.UNKNOWN
    )


def test_identity_revision_cas_and_profile_single_owner(seeded: SiteStrategyService) -> None:
    current = seeded.repository.get_identity("barrons-1")
    assert current is not None
    updated = seeded.apply_identity(
        current.model_copy(
            update={"access": current.access.model_copy(update={"min_interval_ms": 3500})}
        ),
        expected_revision=current.revision,
        actor="test",
    )
    assert updated.revision == current.revision + 1
    with pytest.raises(RuntimeError, match="revision conflict"):
        seeded.apply_identity(current, expected_revision=current.revision, actor="stale")
    with pytest.raises(ValueError, match="already owned"):
        seeded.repository.apply_identity(
            current.model_copy(update={"identity_id": "duplicate-owner", "revision": 0}),
            expected_revision=None,
            actor="test",
        )


def test_identity_only_combination_is_materialized_from_registry(
    seeded: SiteStrategyService,
) -> None:
    spec = seeded.repository.get_strategy("barrons")
    assert spec is not None
    updated = with_identity_bindings(spec, {"barrons-1": "barrons-1", "barrons-2": "barrons-2"})
    seeded.apply_strategy(updated, expected_revision=spec.revision, actor="test")
    resolved = seeded.resolve("https://www.barrons.com/articles/example")
    combination = seeded._purpose_combinations(resolved, SitePurpose.BODY)[0]
    assert combination.identity_id == "barrons-1"
    assert combination.profile_id == "barrons-1"
    assert combination.egress_id == "us-standard-5"


@pytest.mark.asyncio
async def test_runtime_manager_selects_both_tracks_and_serializes_acquire() -> None:
    calls: list[tuple[str, str]] = []

    class Lease:
        page = object()
        browser_cdp = object()
        provenance = SimpleNamespace()

        async def __aenter__(self):
            return self.page

        async def __aexit__(self, exc_type, exc, traceback):
            del exc_type, exc, traceback

    class Adapter:
        def __init__(self, kind: str) -> None:
            self.kind = kind

        async def start(self) -> None:
            pass

        async def close(self) -> None:
            pass

        async def page(self, identity, egress, *, deadline=None):
            del egress, deadline
            calls.append((self.kind, identity.identity_id))
            await asyncio.sleep(0.01)
            return Lease()

        async def stop_identity(self, identity_id, *, reason):
            del identity_id, reason
            return True

        async def status(self, identity_id):
            return {"identity_id": identity_id}

    manager = BrowserRuntimeManager(Adapter("managed"), Adapter("external"))
    managed = BrowserIdentitySpec(
        identity_id="managed", profile_id="managed", egress_id="server-direct"
    )
    external = managed.model_copy(
        update={
            "identity_id": "external",
            "profile_id": "external",
            "runtime_kind": BrowserRuntimeKind.EXTERNAL_CHROME,
            "browser_release": "google-chrome-153.0.8010.52",
        }
    )
    egress = SimpleNamespace()
    leases = await asyncio.gather(
        manager.page(managed, egress),
        manager.page(external, egress),
        manager.page(external, egress),
    )
    await asyncio.gather(*(lease.__aexit__(None, None, None) for lease in leases))
    assert calls.count(("managed", "managed")) == 1
    assert calls.count(("external", "external")) == 2


@pytest.mark.asyncio
async def test_joint_budget_never_holds_one_dimension_while_waiting_for_other() -> None:
    budget = JointBudget()
    entered: list[str] = []
    release = asyncio.Event()

    async def run(site: str) -> None:
        async with budget.permit(
            SitePurpose.BODY,
            site_key=site,
            identity_id="shared",
            site_max_concurrency=1,
            site_min_interval_ms=0,
            identity_max_concurrency=1,
            identity_min_interval_ms=0,
            timeout_seconds=2,
        ):
            entered.append(site)
            if site == "a":
                await release.wait()

    first = asyncio.create_task(run("a"))
    await asyncio.sleep(0.02)
    second = asyncio.create_task(run("b"))
    await asyncio.sleep(0.02)
    assert entered == ["a"]
    release.set()
    await asyncio.gather(first, second)
    assert entered == ["a", "b"]
