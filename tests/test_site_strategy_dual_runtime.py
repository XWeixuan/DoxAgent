from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from doxagent.site_strategy.browser_runtime import BrowserRuntimeManager, RuntimeProvenance
from doxagent.site_strategy.budget import JointBudget
from doxagent.site_strategy.chrome_supervisor import ChromeSupervisor
from doxagent.site_strategy.identity_migration import with_identity_bindings
from doxagent.site_strategy.identity_rollout import apply_first_wave, rollout_plan
from doxagent.site_strategy.repository import SiteStrategyRepository
from doxagent.site_strategy.schema import (
    AuthState,
    BrowserIdentitySpec,
    BrowserRuntimeKind,
    IdentityOperationalState,
    SiteIdentityAuth,
    SitePurpose,
)
from doxagent.site_strategy.seeds import bootstrap_seed
from doxagent.site_strategy.service import SiteStrategyService
from doxagent.site_strategy.supervisor_client import ChromeSupervisorClient


def test_supervisor_owns_stable_network_namespace() -> None:
    compose_path = Path(__file__).resolve().parents[1] / "docker-compose.v2-production.yml"
    compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
    services = compose["services"]
    site_access = services["v2-site-access"]
    supervisor = services["v2-chrome-supervisor"]

    assert site_access["network_mode"] == "service:v2-chrome-supervisor"
    dependency = site_access["depends_on"]["v2-chrome-supervisor"]
    assert dependency == {"condition": "service_healthy", "restart": True}
    assert "ports" not in site_access
    assert supervisor["ports"] == ["127.0.0.1:5900:5900"]
    assert "v2-site-access" in supervisor["networks"]["default"]["aliases"]

    # env_file values do not participate in Compose ${...} interpolation.  A
    # defaulted value in the explicit environment mapping would therefore
    # overwrite DOXAGENT_SITE_ACCESS_ENABLED=true from production .env.v2.
    assert "DOXAGENT_SITE_ACCESS_ENABLED" not in compose["x-environment"]
    for service_name in ("v2-message-bus", "v2-content-enrichment"):
        assert "site_access_worker_token" in services[service_name]["secrets"]
        assert services[service_name]["depends_on"]["v2-site-access"] == {
            "condition": "service_healthy"
        }


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


@pytest.mark.asyncio
async def test_external_prewarm_projects_available_runtime_state(
    seeded: SiteStrategyService, monkeypatch: pytest.MonkeyPatch
) -> None:
    apply_first_wave(seeded.repository)

    async def prewarm(identity, egress):
        del egress
        return RuntimeProvenance(
            identity_id=identity.identity_id,
            identity_revision=identity.revision,
            runtime_kind=BrowserRuntimeKind.EXTERNAL_CHROME,
            instance_id=f"instance-{identity.identity_id}",
            generation=1,
        )

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(seeded.runtime.browser_runtimes, "prewarm", prewarm)
    monkeypatch.setattr("doxagent.site_strategy.service.asyncio.sleep", no_sleep)
    await seeded._prewarm_identities()

    for identity_id in ("dowjones-main", "seeking-alpha-main", "yahoo-main"):
        runtime = seeded.repository.get_identity_runtime(identity_id)
        assert runtime.operational_state is IdentityOperationalState.AVAILABLE
        assert runtime.instance_id == f"instance-{identity_id}"


@pytest.mark.asyncio
async def test_supervisor_client_waits_for_late_socket(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    attempts = 0

    async def connect(_path):
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise FileNotFoundError
        reader = asyncio.StreamReader()
        reader.feed_data(b'{"ok":true,"stopped":false}\n')
        reader.feed_eof()

        class Writer:
            def write(self, _value):
                return None

            async def drain(self):
                return None

            def close(self):
                return None

            async def wait_closed(self):
                return None

        return reader, Writer()

    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "open_unix_connection", connect, raising=False)
    monkeypatch.setattr("doxagent.site_strategy.supervisor_client.asyncio.sleep", no_sleep)
    client = ChromeSupervisorClient(tmp_path / "late.sock", owner_id="test")
    assert await client.stop("identity", reason="test") is False
    assert attempts == 3


def test_supervisor_removes_only_known_singleton_links(tmp_path: Path) -> None:
    try:
        for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
            (tmp_path / name).symlink_to(f"stale-{name}")
    except OSError:
        pytest.skip("this Windows account cannot create symbolic links")
    ChromeSupervisor._clear_runtime_singletons(tmp_path)
    assert not any(tmp_path.iterdir())


def test_supervisor_rejects_non_link_singleton(tmp_path: Path) -> None:
    unknown = tmp_path / "SingletonLock"
    unknown.write_text("not a runtime link", encoding="utf-8")
    with pytest.raises(RuntimeError, match="profile_busy_unknown_singleton"):
        ChromeSupervisor._clear_runtime_singletons(tmp_path)
    assert unknown.read_text(encoding="utf-8") == "not a runtime link"


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
