from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from cdecr.config import CDECRSettings
from doxagent.cdecr_integration.contracts import (
    RuntimeRegistryBinding,
    TickerJobMode,
    TickerJobStage,
    TickerJobState,
    TickerPipelineResult,
)
from doxagent.cdecr_integration.coordinator import TickerCDECRPipelineCoordinator
from doxagent.cdecr_integration.prebuilt import (
    CDECRPrebuiltManifest,
    CDECRPrebuiltStore,
    RegistryArtifact,
    configuration_fingerprint,
    message_set_sha256,
    sha256_file,
)
from doxagent.message_bus_v2.repository import MessageBusV2Repository
from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.persistent_runtime_v2.journal import RuntimeJournal
from doxagent.runtime_scheduler.repository import SQLiteRuntimeSchedulerRepository
from doxagent.settings import DoxAgentSettings
from doxagent.ticker_initialization.catalog import default_plan
from doxagent.ticker_initialization.repository import InitializationRepository
from doxagent.ticker_initialization.research_adapter import ResearchInitializationAdapter
from doxagent.ticker_initialization.schema import NodeResult, NodeSpec, RunStatus
from doxagent.ticker_initialization.service import InitializationWorker, NodeContext
from doxagent.v2_control.repository import ControlError, ControlRepository
from doxagent.v2_control.service import ControlService


def _ready_bundle(store: CDECRPrebuiltStore, ticker: str, now: datetime) -> Path:
    work = store.root / "incoming" / "work"
    work.mkdir()
    registry = work / "runtime.sqlite3"
    messages = ["message-1"]
    with closing(sqlite3.connect(registry)) as db:
        db.executescript(
            """
            CREATE TABLE doxagent_ticker_binding (
                singleton INTEGER PRIMARY KEY, market TEXT, ticker TEXT,
                runtime_scope TEXT, binding_version TEXT
            );
            CREATE TABLE bulk_epochs (
                epoch_id TEXT PRIMARY KEY, status TEXT, message_ids_json TEXT
            );
            CREATE TABLE source_messages (message_id TEXT PRIMARY KEY);
            """
        )
        db.execute(
            "INSERT INTO doxagent_ticker_binding VALUES(1,?,?,?,?)",
            ("US", ticker, f"cdecr:US:{ticker}", "cdecr-per-ticker-registry-v1"),
        )
        db.execute(
            "INSERT INTO bulk_epochs VALUES('epoch-1','FINALIZED',?)", (json.dumps(messages),)
        )
        db.execute("INSERT INTO source_messages VALUES(?)", (messages[0],))
        db.commit()
    digest = sha256_file(registry)
    bundle_id = f"cdecr-US-{ticker}-20260909T020000Z-{digest[-12:]}"
    bundle = work.with_name(bundle_id)
    work.rename(bundle)
    registry = bundle / "runtime.sqlite3"
    manifest = CDECRPrebuiltManifest(
        bundle_id=bundle_id,
        market="US",
        ticker=ticker,
        runtime_scope=f"cdecr:US:{ticker}",
        research_cutoff_at=now - timedelta(hours=1),
        generated_at=now,
        code_revision="test-revision",
        compatibility_version="cdecr-prebuilt-runtime-v1",
        configuration_fingerprint=configuration_fingerprint(CDECRSettings()),
        epoch_id="epoch-1",
        message_ids=messages,
        message_set_sha256=message_set_sha256(messages),
        document_count=1,
        eligible_document_count=1,
        registry=RegistryArtifact(size_bytes=registry.stat().st_size, sha256=digest),
    )
    (bundle / "manifest.json").write_text(manifest.model_dump_json(), encoding="utf-8")
    return store.publish(
        bundle,
        compatibility_version=manifest.compatibility_version,
        fingerprint=manifest.configuration_fingerprint,
    ).path


def _control_service(
    tmp_path: Path, settings: DoxAgentSettings
) -> tuple[ControlService, ControlRepository]:
    control = ControlRepository(RuntimeJournal(tmp_path / "runtime.sqlite3"))
    control.migrate()
    service = ControlService(
        control,
        InitializationRepository(tmp_path / "initialization.sqlite3"),
        MessageBusV2Service(MessageBusV2Repository(tmp_path / "bus.sqlite3")),
        SQLiteRuntimeSchedulerRepository(tmp_path / "scheduler.sqlite3"),
        settings=settings,
    )
    return service, control


def test_control_claims_bundle_and_pins_cutoff_and_node_reference(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    store = CDECRPrebuiltStore(tmp_path / "prebuilt")
    _ready_bundle(store, "MU", now)
    settings = DoxAgentSettings(
        _env_file=None,
        cdecr_execution_mode="PREBUILT_REQUIRED",
        cdecr_prebuilt_root=str(store.root),
    )
    service, control = _control_service(tmp_path, settings)
    operation = control.submit(
        "MU",
        "START",
        actor="test",
        key="request-1",
        body={"monitor_mode": "MESSAGE_MONITORING", "initialization": "FORCE_INITIALIZE"},
    )
    assert service.step(operation)
    run = service.initialization.by_control_operation(operation["id"])
    assert run is not None and run.research_cutoff_at == now - timedelta(hours=1)
    cdecr = next(
        node for node in service.initialization.nodes(run.initialization_id) if node.key == "cdecr"
    )
    reference = cdecr.inputs["_prebuilt_cdecr"]
    assert reference["claim_owner"] == operation["id"]
    assert (store.root / "claimed" / operation["id"] / reference["bundle_id"]).is_dir()


def test_required_mode_fails_without_bundle_and_reuse_semantics_stay_first(tmp_path: Path) -> None:
    settings = DoxAgentSettings(
        _env_file=None,
        cdecr_execution_mode="PREBUILT_REQUIRED",
        cdecr_prebuilt_root=str(tmp_path / "prebuilt"),
    )
    service, control = _control_service(tmp_path, settings)
    required = control.submit(
        "MU",
        "START",
        actor="test",
        key="request-1",
        body={"monitor_mode": "MESSAGE_MONITORING", "initialization": "FORCE_INITIALIZE"},
    )
    with pytest.raises(ControlError, match="CDECR_PREBUILT_REQUIRED"):
        service.step(required)

    other_root = tmp_path / "reuse"
    other_root.mkdir()
    reuse, reuse_control = _control_service(other_root, settings)
    operation = reuse_control.submit(
        "AMD",
        "START",
        actor="test",
        key="request-2",
        body={"monitor_mode": "MESSAGE_MONITORING", "initialization": "REUSE_ACTIVE"},
    )
    with pytest.raises(ControlError, match="NO_ACTIVE_REVISION"):
        reuse.step(operation)


def test_submit_failure_releases_claim_when_no_run_was_created(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    store = CDECRPrebuiltStore(tmp_path / "prebuilt")
    _ready_bundle(store, "MU", now)
    settings = DoxAgentSettings(
        _env_file=None,
        cdecr_execution_mode="PREBUILT_REQUIRED",
        cdecr_prebuilt_root=str(store.root),
    )
    service, control = _control_service(tmp_path, settings)
    service.initialization.submit("MU", now, [NodeSpec(key="existing", block="D1")])
    operation = control.submit(
        "MU",
        "START",
        actor="test",
        key="request-1",
        body={"monitor_mode": "MESSAGE_MONITORING", "initialization": "FORCE_INITIALIZE"},
    )
    with pytest.raises(RuntimeError, match="DUPLICATE_ACTIVE_INITIALIZATION"):
        service.step(operation)
    assert service.initialization.by_control_operation(operation["id"]) is None
    assert len(store.list_ready(ticker="MU")) == 1


def test_default_plan_only_changes_cdecr_inputs() -> None:
    reference = {
        "contract_version": "cdecr-prebuilt-ref-v1",
        "bundle_id": "bundle",
        "claim_owner": "operation",
        "registry_sha256": "sha256:" + "a" * 64,
        "epoch_id": "epoch",
    }
    baseline = default_plan()
    adopted = default_plan(cdecr_prebuilt_ref=reference)
    assert [node.dependencies for node in baseline] == [node.dependencies for node in adopted]
    assert next(node for node in baseline if node.key == "cdecr").inputs == {}
    assert next(node for node in adopted if node.key == "cdecr").inputs == {
        "_prebuilt_cdecr": reference
    }


def test_seeded_prebuilt_job_is_idempotent(tmp_path: Path) -> None:
    coordinator = TickerCDECRPipelineCoordinator(
        registry_root=tmp_path / "registry",
        state_root=tmp_path / "state",
        event_library_root=tmp_path / "events",
        providers=[],
        runtime_factory=lambda binding: (_ for _ in ()).throw(AssertionError(binding)),
    )
    cutoff = datetime.now(UTC) - timedelta(hours=1)
    first = coordinator.seed_prebuilt_job(
        market="US", ticker="MU", as_of=cutoff, message_ids=["message-1"], epoch_id="epoch-1"
    )
    second = coordinator.seed_prebuilt_job(
        market="US", ticker="MU", as_of=cutoff, message_ids=["message-1"], epoch_id="epoch-1"
    )
    assert first == second
    assert second.stage is TickerJobStage.CDECR_RUNNING


@pytest.mark.asyncio
async def test_adapter_required_mode_never_falls_back_to_local_cdecr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = InitializationRepository(tmp_path / "initialization" / "control.sqlite3")
    run = repository.submit(
        "MU",
        datetime.now(UTC) - timedelta(hours=1),
        [NodeSpec(key="cdecr", block="CDECR")],
    )
    lease = repository.claim("worker")
    assert lease is not None
    node = repository.begin(lease, "cdecr", {})

    class Worker:
        async def aclose(self) -> None:
            return None

    monkeypatch.setattr(
        "doxagent.ticker_initialization.research_adapter.HttpCodexWorkerClient",
        lambda *args, **kwargs: Worker(),
    )
    settings = DoxAgentSettings(
        _env_file=None,
        codex_worker_bearer_token="worker-token",
        codex_capability_secret="capability-token",
        event_library_root=str(tmp_path / "events"),
        codex_runtime_sqlite_path=str(tmp_path / "research.sqlite3"),
        cdecr_execution_mode="PREBUILT_REQUIRED",
    )
    with pytest.raises(RuntimeError, match="CDECR_PREBUILT_REQUIRED"):
        await ResearchInitializationAdapter(settings).execute(NodeContext(repository, lease, node))
    assert repository.get(run.initialization_id).status.value == "RUNNING"
    assert not (
        tmp_path / "initialization" / "workspaces" / run.initialization_id / "registry"
    ).exists()


@pytest.mark.asyncio
async def test_post_complete_action_retries_without_invalidating_node(tmp_path: Path) -> None:
    repository = InitializationRepository(tmp_path / "control.sqlite3")
    run = repository.submit("MU", datetime.now(UTC), [NodeSpec(key="cdecr", block="CDECR")])

    class Adapter:
        calls = 0

        async def reconcile(self, context: NodeContext) -> NodeResult | None:
            return None

        async def execute(self, context: NodeContext) -> NodeResult:
            return NodeResult(artifacts={"cdecr": {}})

        async def after_complete(self, context: NodeContext, result: NodeResult) -> None:
            self.calls += 1
            if self.calls == 1:
                raise OSError("temporary consume failure")

    adapter = Adapter()
    result = await InitializationWorker(repository, lambda node: adapter).run_once()
    assert result is not None and result.status is RunStatus.SUCCEEDED
    assert adapter.calls == 2
    node = repository.nodes(run.initialization_id)[0]
    assert node.status == "SUCCEEDED"
    assert "post_complete_warning" not in node.receipt


@pytest.mark.asyncio
async def test_adapter_imports_prebuilt_and_skips_cdecr_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    store = CDECRPrebuiltStore(tmp_path / "prebuilt")
    _ready_bundle(store, "MU", now)
    claimed = store.claim_ready(
        market="US",
        ticker="MU",
        operation_id="operation-1",
        compatibility_version="cdecr-prebuilt-runtime-v1",
        fingerprint=configuration_fingerprint(CDECRSettings()),
        max_age_hours=24,
    )
    assert claimed is not None
    reference, manifest = claimed
    repository = InitializationRepository(tmp_path / "initialization" / "control.sqlite3")
    run = repository.submit(
        "MU",
        manifest.research_cutoff_at,
        [NodeSpec(key="cdecr", block="CDECR", inputs={"_prebuilt_cdecr": reference.model_dump()})],
        control_operation_id="operation-1",
    )
    lease = repository.claim("worker")
    assert lease is not None
    node = repository.begin(lease, "cdecr", {})
    context = NodeContext(repository, lease, node)
    calls = {"seed": 0, "execute": 0}

    class Worker:
        async def aclose(self) -> None:
            return None

    class Coordinator:
        def __init__(self, **kwargs: object) -> None:
            self.registry_root = Path(str(kwargs["registry_root"]))

        def binding_for(self, *, market: str, ticker: str) -> RuntimeRegistryBinding:
            return RuntimeRegistryBinding(
                market=market,
                ticker=ticker,
                runtime_scope=f"cdecr:{market}:{ticker}",
                registry_path=str(self.registry_root / market / ticker / "runtime.sqlite3"),
            )

        def seed_prebuilt_job(self, **kwargs: object) -> None:
            calls["seed"] += 1

        async def prepare_runtime_through_delta(self, **kwargs: object) -> TickerPipelineResult:
            target = self.registry_root / "US" / "MU" / "runtime.sqlite3"
            assert sha256_file(target) == reference.registry_sha256
            return TickerPipelineResult(
                job=TickerJobState(
                    job_id="job-1",
                    market="US",
                    ticker="MU",
                    mode=TickerJobMode.INITIALIZE,
                    as_of=manifest.research_cutoff_at,
                    stage=TickerJobStage.DELTA_READY,
                    runtime_scope="cdecr:US:MU",
                    registry_path=str(target),
                    staging_path=str(tmp_path / "staging.sqlite3"),
                    event_library_path=str(tmp_path / "events.sqlite3"),
                    message_ids=manifest.message_ids,
                    epoch_id=manifest.epoch_id,
                    runtime_snapshot_id="snapshot-1",
                    delta_batch_id="delta-1",
                    updated_at=now,
                ),
                delta_batch_id="delta-1",
            )

    monkeypatch.setattr(
        "doxagent.ticker_initialization.research_adapter.HttpCodexWorkerClient",
        lambda *args, **kwargs: Worker(),
    )
    monkeypatch.setattr(
        "doxagent.ticker_initialization.research_adapter.TickerCDECRPipelineCoordinator",
        Coordinator,
    )
    settings = DoxAgentSettings(
        _env_file=None,
        codex_worker_bearer_token="worker-token",
        codex_capability_secret="capability-token",
        event_library_root=str(tmp_path / "events"),
        codex_runtime_sqlite_path=str(tmp_path / "research.sqlite3"),
        cdecr_prebuilt_root=str(store.root),
    )
    adapter = ResearchInitializationAdapter(settings)
    result = await adapter.execute(context)
    assert calls == {"seed": 1, "execute": 0}
    assert result.quality_annotations == ["CDECR_PREBUILT_ADOPTED"]
    repository.complete(lease, "cdecr", result)
    await adapter.after_complete(context, result)
    assert (store.root / "consumed" / run.initialization_id / reference.bundle_id).is_dir()
