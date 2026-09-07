import asyncio
from datetime import UTC, datetime
from threading import Event
from types import SimpleNamespace

import pytest

from doxagent.message_bus_v2.service import MessageBusV2Service
from doxagent.ticker_initialization import InitializationRepository, NodeResult, NodeSpec
from doxagent.ticker_initialization.configuration import CandidateConfiguration
from doxagent.ticker_initialization.consumers import (
    admit_bus_revisions,
    admit_runtime_revisions,
    consumer_heartbeat,
)


@pytest.mark.parametrize(
    "operation,same_config,preserve_live",
    [
        ("REPLACE_ARTIFACT", True, True),
        ("ROLLBACK", True, False),
        ("ACTIVATE", False, False),
    ],
)
def test_research_activation_preserves_effective_monitoring_edits(
    tmp_path, monkeypatch, operation, same_config, preserve_live
):
    from doxagent.ticker_initialization import activation_adapter as module

    bus_path = tmp_path / "bus.db"
    original = CandidateConfiguration(bus_path, "original", "MU")
    MessageBusV2Service(original.live).bootstrap()
    MessageBusV2Service(original.live).materialize_default_bindings("MU")
    original.prepare()
    binding = original.live.list_bindings(ticker="MU")[0]
    original.live.save_binding(binding.model_copy(update={"enabled": False}))
    refs = {key: {} for key in ("document1", "document2")}
    refs.update(
        document3={"version": 1},
        event_library={"version": 1},
        monitoring_configuration={"initialization_id": "original"},
    )
    repository = SimpleNamespace(
        active_revision=lambda _: {
            "artifacts": {
                "monitoring_configuration": {
                    "initialization_id": "original" if same_config else "different"
                }
            }
        },
        stage_revision=lambda lease, identity, selected: selected,
    )
    context = SimpleNamespace(
        run=SimpleNamespace(initialization_id="replacement", ticker="MU", operation_kind=operation),
        node=SimpleNamespace(key="activation.prepare", dependencies=[], inputs={"artifacts": refs}),
        repository=repository,
        lease=None,
    )
    monkeypatch.setattr(
        module,
        "SQLiteDocument3PolicyRepository",
        lambda _: SimpleNamespace(get_projection=lambda *args: object()),
    )
    monkeypatch.setattr(
        module,
        "PublishedEventLibraryReader",
        lambda *args, **kwargs: SimpleNamespace(known_index=lambda *args, **kwargs: object()),
    )
    adapter = module.ActivationAdapter(
        SimpleNamespace(
            message_bus_v2_sqlite_path=bus_path,
            codex_runtime_sqlite_path=tmp_path / "runtime.db",
            event_library_root=tmp_path,
        )
    )
    monkeypatch.setattr(adapter, "_documents_available", lambda *args: None)

    async def ready():
        pass

    monkeypatch.setattr(adapter, "_w3_ready", ready)
    asyncio.run(adapter.execute(context))
    candidate = CandidateConfiguration(bus_path, "replacement", "MU")
    copied = next(
        item
        for item in candidate.candidate.list_bindings(ticker="MU")
        if item.binding_id == binding.binding_id
    )
    assert copied.enabled is (not preserve_live)


def test_long_case_heartbeat_refreshes_only_captured_revision_and_stops(tmp_path, monkeypatch):
    control = InitializationRepository(tmp_path / "control.db")
    _, _, _, identity = stage(control, tmp_path / "bus.db")
    control.acknowledge_revision("MU", identity, "runtime")
    original = control.acknowledge_revision
    pulsed = Event()
    refreshed = []

    def acknowledge(ticker, revision, worker):
        refreshed.append(revision)
        result = original(ticker, revision, worker)
        pulsed.set()
        return result

    monkeypatch.setattr(control, "acknowledge_revision", acknowledge)
    with consumer_heartbeat(control, "runtime", lambda _: True, interval=0.01):
        assert pulsed.wait(3)
    assert refreshed and set(refreshed) == {identity}
    assert control.revision_acknowledged("MU", identity, "runtime")


def stage(control, bus_path, *, reinitialize=False):
    run = control.submit(
        "MU",
        datetime.now(UTC),
        [NodeSpec(key="commit", block="ACTIVATION")],
        reinitialize=reinitialize,
    )
    lease = control.claim("worker")
    config = CandidateConfiguration(bus_path, run.initialization_id, "MU")
    MessageBusV2Service(config.live).bootstrap()
    config.prepare()
    identity = run.initialization_id + "-activation"
    control.stage_revision(
        lease, identity, {"monitoring_configuration": {"initialization_id": run.initialization_id}}
    )
    control.begin(lease, "commit", {})
    control.activate(lease, identity)
    return run, lease, config, identity


def test_ack_only_comes_from_consumer_admission_and_rejects_stale_revision(tmp_path):
    control = InitializationRepository(tmp_path / "control.db")
    run, lease, config, identity = stage(control, tmp_path / "bus.db")
    assert not control.revision_acknowledged("MU", identity, "bus")
    bus = MessageBusV2Service(config.live)
    admit_bus_revisions(control, bus)
    assert control.revision_acknowledged("MU", identity, "bus")
    assert config.current_head() == run.initialization_id
    assert config.live.get_ticker_state("MU").status.value == "running"
    calls = []
    scheduler = SimpleNamespace(
        repository=SimpleNamespace(get_state=lambda _: None),
        admit_activation=lambda ticker, revision: calls.append((ticker, revision)),
    )
    assert admit_runtime_revisions(control, scheduler) == {"MU"}
    assert calls == [("MU", identity)]
    assert control.revision_acknowledged("MU", identity, "runtime")
    assert not control.acknowledge_revision("MU", "stale-revision", "runtime")


def test_replacement_rollback_restores_config_and_same_operation_can_be_reactivated(tmp_path):
    control = InitializationRepository(tmp_path / "control.db")
    run, lease, old, first = stage(control, tmp_path / "bus.db")
    bus = MessageBusV2Service(old.live)
    admit_bus_revisions(control, bus)
    before = old.live.list_bindings(ticker="MU")
    control.complete(lease, "commit", NodeResult())
    control.finish(lease)
    newer, lease, candidate, second = stage(control, tmp_path / "bus.db", reinitialize=True)
    binding = candidate.candidate.list_bindings(ticker="MU")[0]
    candidate.candidate.save_binding(binding.model_copy(update={"enabled": False}))
    admit_bus_revisions(control, bus)
    assert candidate.current_head() == newer.initialization_id
    assert control.rollback_revision(lease, second)
    admit_bus_revisions(control, bus)
    assert old.current_head() == run.initialization_id
    assert old.live.list_bindings(ticker="MU") == before
    assert control.revision_acknowledged("MU", first, "bus")
    control.activate(lease, second)
    admit_bus_revisions(control, bus)
    assert candidate.current_head() == newer.initialization_id
    assert control.revision_acknowledged("MU", second, "bus")
